"""End-to-end test of the ETL layer over the HTTP API.

Every other test in this package stops at the edge of the machinery: the run
manager's subprocesses print and exit, and catalog discovery is mocked. Nothing
verifies that the layer actually *moves data* - that a run started through the
API extracts records, hands them to a loader, and leaves a bookmark behind.

This does, using a Singer tap and target written to a temporary directory. They
are the smallest programs that speak the protocol honestly, which keeps the
test free of a network round trip, a virtualenv build, and any dependency on a
connector staying published. What is under test is Meltano's ELT machinery and
this server's control of it, not a third-party plugin.

The exception is the mapper, which cannot be faked: whether the stream maps
this server compiles are the dialect `meltano-map-transformer` actually speaks
is a question only the real mapper can answer. Those tests skip unless it is on
`PATH`, so a plain run of this suite still owes nothing to pip.
"""

from __future__ import annotations

import contextlib
import json
import shutil
import sys
import time
import typing as t

import pytest

from meltano.core.job import Job
from meltano.core.plugin import PluginType
from meltano.core.plugin.project_plugin import ProjectPlugin
from meltano.core.project_plugins_service import PluginAlreadyAddedException
from meltano.ui.services.transforms import compile_steps
from tests.meltano.ui.transform_cases import CASES, Case
from tests.meltano.ui.transform_cases import RECORDS as CASE_RECORDS

if t.TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from fastapi.testclient import TestClient

    from meltano.core.project import Project
    from meltano.ui.context import AppContext

#: A run of two records should take well under a second; this is the ceiling
#: before the test gives up and reports what it saw.
_RUN_TIMEOUT_SECONDS = 60

#: The console script `meltano-map-transform` installs. Everything about the
#: mapper chain that this server cannot control lives behind this name: whether
#: the stream maps it compiles are the dialect the real mapper speaks.
_MAPPER_EXECUTABLE = "meltano-map-transform"

#: Emits one stream, two records, and a bookmark. Deliberately not a full tap:
#: no discovery, no catalog, no state reading.
_TAP_SOURCE = '''\
"""A minimal Singer tap: one stream, two records, one bookmark."""

import json

print(
    json.dumps(
        {
            "type": "SCHEMA",
            "stream": "widgets",
            "key_properties": ["id"],
            "schema": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "name": {"type": "string"},
                },
            },
        },
    ),
)
for index, name in enumerate(("alpha", "beta"), start=1):
    print(
        json.dumps(
            {
                "type": "RECORD",
                "stream": "widgets",
                "record": {"id": index, "name": name},
            },
        ),
    )
print(
    json.dumps(
        {"type": "STATE", "value": {"bookmarks": {"widgets": {"id": 2}}}},
    ),
)
'''

#: Writes records where the test can find them and echoes state, which is what
#: makes Meltano persist a bookmark.
_TARGET_SOURCE = '''\
"""A minimal Singer target: records to a file, state back to stdout."""

import json
import os
import sys

with open(os.environ["FAKE_TARGET_OUT"], "w", encoding="utf-8") as handle:
    for line in sys.stdin:
        message = json.loads(line)
        if message["type"] == "RECORD":
            handle.write(json.dumps(message["record"]) + "\\n")
        elif message["type"] == "STATE":
            print(json.dumps(message["value"]), flush=True)
'''


def _write_executable(path: Path, source: str) -> Path:
    """Write a runnable script that uses the interpreter running these tests.

    The shebang is `sys.executable` rather than `/usr/bin/env python3` so the
    script cannot pick up a different interpreter than the suite is using.

    Args:
        path: Where to write it.
        source: The script body.

    Returns:
        The path, now executable.
    """
    path.write_text(f"#!{sys.executable}\n{source}")
    path.chmod(0o755)
    return path


@pytest.fixture
def singer_pipeline(
    project: Project,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    """Declare a runnable tap and loader in the project.

    Declared directly rather than through `POST /plugins`, which adds from
    Meltano Hub: these plugins exist only for this test. Neither has a
    `pip_url`, so Meltano invokes the executable without building a venv.

    Args:
        project: The test project.
        tmp_path: Scratch space for the scripts and the loader's output.
        monkeypatch: Sets the loader's output path for the run subprocess.

    Yields:
        The file the loader will write its records to.
    """
    out = tmp_path / "widgets.jsonl"

    # Inherited by the run subprocess, which is spawned from this process.
    monkeypatch.setenv("FAKE_TARGET_OUT", str(out))

    added = [
        _plugin(
            "extractors",
            "fake-tap",
            _write_executable(tmp_path / "fake-tap", _TAP_SOURCE),
        ),
        _plugin(
            "loaders",
            "fake-target",
            _write_executable(tmp_path / "fake-target", _TARGET_SOURCE),
        ),
    ]
    for plugin in added:
        project.plugins.add_to_file(plugin)

    try:
        yield out
    finally:
        # The `project` fixture is class-scoped, so these would otherwise be
        # visible to whatever runs next.
        for plugin in added:
            project.plugins.remove_from_file(plugin)


def _plugin(plugin_type: str, name: str, executable: Path) -> ProjectPlugin:
    """Build a project plugin backed by a local executable.

    Args:
        plugin_type: Plural plugin type.
        name: The plugin's name.
        executable: The script to invoke.

    Returns:
        The plugin, ready to add to the project.
    """
    return ProjectPlugin(
        PluginType.from_cli_argument(plugin_type),
        name,
        namespace=name.replace("-", "_"),
        executable=str(executable),
    )


def _await_run(client: TestClient, run_id: str) -> dict[str, t.Any]:
    """Poll a run until it reaches a terminal state.

    Args:
        client: The authenticated client.
        run_id: The run to wait for.

    Returns:
        The final run record.

    Raises:
        AssertionError: If the run does not finish in time.
    """
    terminal = {"success", "failed", "cancelled", "unknown"}
    deadline = time.monotonic() + _RUN_TIMEOUT_SECONDS

    while time.monotonic() < deadline:
        body = client.get(f"/api/v1/runs/{run_id}").json()
        if body["status"] in terminal:
            return body
        time.sleep(0.1)

    log = client.get(f"/api/v1/runs/{run_id}/log").json()
    msg = f"Run {run_id} did not finish. Output:\n" + "\n".join(log["lines"])
    raise AssertionError(msg)


@pytest.mark.slow
@pytest.mark.usefixtures("singer_pipeline")
class TestPipelineEndToEnd:
    """A real extraction, driven entirely through the HTTP API."""

    def test_a_run_started_over_http_moves_records(
        self,
        ui_client: TestClient,
        singer_pipeline: Path,
    ) -> None:
        """The whole point: data goes in one end and out the other.

        Every other test in this package would still pass if the run manager
        spawned a process that did nothing at all.
        """
        started = ui_client.post(
            "/api/v1/runs",
            json={"blocks": ["fake-tap", "fake-target"]},
        )
        assert started.status_code == 202

        run = _await_run(ui_client, started.json()["run_id"])
        assert run["status"] == "success", run
        assert run["exit_code"] == 0

        rows = [
            json.loads(line)
            for line in singer_pipeline.read_text().splitlines()
            if line
        ]
        assert rows == [{"id": 1, "name": "alpha"}, {"id": 2, "name": "beta"}]

    def test_the_run_is_correlated_with_its_job_rows(
        self,
        ui_client: TestClient,
        ui_context: AppContext,
    ) -> None:
        """The run's `run_id` reaches the system database and back again.

        This is the correlation the runs endpoint depends on to show history
        for runs it did not supervise. Asserting it against a real run, rather
        than a seeded row, is what proves the identifier actually survives the
        trip through `meltano run --run-id`.
        """
        started = ui_client.post(
            "/api/v1/runs",
            json={"blocks": ["fake-tap", "fake-target"]},
        )
        run_id = started.json()["run_id"]
        run = _await_run(ui_client, run_id)

        assert run["status"] == "success", run
        assert run["has_log"] is True
        assert [job["job_name"] for job in run["jobs"]] == [
            "dev:fake-tap-to-fake-target",
        ]
        assert run["jobs"][0]["state"] == "SUCCESS"
        # Set by `RunManager.start`, so a run begun here is distinguishable
        # from one begun in a terminal.
        assert run["jobs"][0]["trigger"] == "ui"

        session = ui_context.session_factory()
        try:
            rows = session.query(Job).filter(Job.run_id == run_id).all()
            assert len(rows) == 1
        finally:
            session.close()

    def test_the_run_leaves_a_bookmark(self, ui_client: TestClient) -> None:
        """State written by a real run is readable over the state endpoints.

        The state endpoints are otherwise only tested against state this suite
        wrote itself, which would not catch the bookmark never being persisted.
        """
        started = ui_client.post(
            "/api/v1/runs",
            json={"blocks": ["fake-tap", "fake-target"]},
        )
        run = _await_run(ui_client, started.json()["run_id"])
        assert run["status"] == "success", run

        state_id = "dev:fake-tap-to-fake-target"
        body = ui_client.get(f"/api/v1/state/{state_id}").json()

        assert body["streams"] == ["widgets"]
        assert body["state"]["singer_state"]["bookmarks"]["widgets"] == {"id": 2}

    def test_a_real_extractor_can_be_previewed(
        self,
        ui_client: TestClient,
    ) -> None:
        """Rows come back from an actual tap, shaped by actual steps.

        The preview endpoint's own tests stub the tap out, so this is the only
        check that the Singer output of a real process is parsed, capped, and
        that the tap is stopped afterwards rather than left running.
        """
        response = ui_client.post(
            "/api/v1/plugins/extractors/fake-tap/preview",
            json={
                "stream": "widgets",
                "limit": 10,
                "steps": [
                    {"kind": "rename", "column": "name", "to": "label"},
                    {
                        "kind": "filter",
                        "column": "id",
                        "operator": "gt",
                        "value": 1,
                    },
                ],
            },
        )

        assert response.status_code == 200, response.text
        body = response.json()

        # The tap emits two records; the filter keeps one.
        assert body["read_count"] == 2
        assert body["rows"] == [{"id": 2, "label": "beta"}]
        assert body["schemas"]["widgets"] == ["id", "name"]
        assert body["stream_map"] == {
            "label": "record['name']",
            "name": None,
            "__filter__": "(record['id'] > 1)",
        }

    def test_a_preview_writes_nothing(
        self,
        ui_client: TestClient,
        singer_pipeline: Path,
    ) -> None:
        """A preview must not load, bookmark, or otherwise leave a trace.

        It is run repeatedly while someone is still deciding what they want,
        so a preview with side effects would be worse than no preview.
        """
        before = ui_client.get("/api/v1/state").json()

        ui_client.post(
            "/api/v1/plugins/extractors/fake-tap/preview",
            json={"stream": "widgets"},
        )

        assert ui_client.get("/api/v1/state").json() == before
        assert not singer_pipeline.exists()

    def test_a_job_defined_over_http_is_runnable(
        self,
        ui_client: TestClient,
        singer_pipeline: Path,
    ) -> None:
        """Defining a job and running it by name is one continuous path.

        The jobs and runs endpoints are tested separately elsewhere; this is
        the seam between them, which only holds because a job name is itself a
        valid run block.
        """
        created = ui_client.post(
            "/api/v1/jobs",
            json={"name": "widgets-nightly", "tasks": ["fake-tap fake-target"]},
        )
        assert created.status_code == 201

        try:
            started = ui_client.post(
                "/api/v1/runs",
                json={"blocks": ["widgets-nightly"]},
            )
            assert started.status_code == 202

            run = _await_run(ui_client, started.json()["run_id"])
            assert run["status"] == "success", run
            assert singer_pipeline.read_text().strip()
        finally:
            ui_client.delete("/api/v1/jobs/widgets-nightly")


@pytest.fixture
def real_mapper(project: Project) -> Iterator[ProjectPlugin]:
    """Declare the actual `meltano-map-transformer` in the project.

    Skips unless its console script is on `PATH`. Installing it here instead
    would make every run of this suite depend on pip and on a third-party
    plugin staying published, which is too much to ask of a unit test run; the
    mapper is opted into with `uv pip install meltano-map-transform`.

    It is declared with an `executable` and no `pip_url`, so Meltano invokes
    the script that is already there rather than building a venv for it.

    Args:
        project: The test project.

    Yields:
        The mapper plugin.
    """
    executable = shutil.which(_MAPPER_EXECUTABLE)
    if executable is None:
        pytest.skip(
            f"{_MAPPER_EXECUTABLE} is not on PATH; "
            "install `meltano-map-transform` to run the mapper tests",
        )

    plugin = ProjectPlugin(
        PluginType.MAPPERS,
        "meltano-map-transformer",
        namespace="meltano_map_transformer",
        executable=executable,
    )
    # Added once for the class-scoped project and left in place: removing and
    # re-adding it per test fights the plugin cache, which still reports a
    # mapper the file no longer has. Emptying its mappings is isolation enough.
    with contextlib.suppress(PluginAlreadyAddedException):
        project.plugins.add_to_file(plugin)

    _clear_mappings(project, plugin.name)
    try:
        yield plugin
    finally:
        _clear_mappings(project, plugin.name)


def _clear_mappings(project: Project, mapper_name: str) -> None:
    """Empty a mapper's saved mappings.

    Args:
        project: The test project.
        mapper_name: The mapper to reset.
    """
    with project.config_service.update_meltano_yml() as meltano_yml:
        for plugin in meltano_yml["plugins"]["mappers"]:
            if plugin.name == mapper_name and not plugin.is_mapping():
                plugin.extras["mappings"] = []
                return


def _rows(path: Path) -> list[dict[str, t.Any]]:
    """Read back what the loader wrote.

    Args:
        path: The loader's output file.

    Returns:
        One dict per record, in the order they arrived.
    """
    return [json.loads(line) for line in path.read_text().splitlines() if line]


@pytest.mark.slow
@pytest.mark.usefixtures("singer_pipeline", "real_mapper")
class TestMappingAppliedByARealMapper:
    """The one claim this server cannot make on its own.

    Everything else about mappings is checked against what lands in
    `meltano.yml`, which proves only that the file says what we meant to say.
    Whether `meltano-map-transformer` reads those stream maps the way the
    compiler assumes is a contract with someone else's code, and the only way
    to find out is to put records through it.
    """

    #: Renames a column, drops the one it came from, and filters a record out.
    #: Between them these exercise every construct the compiler emits: an
    #: expression, a `null`, and a `__filter__`.
    STEPS: t.ClassVar[list[dict[str, t.Any]]] = [
        {"kind": "rename", "column": "name", "to": "label"},
        {"kind": "filter", "column": "id", "operator": "gt", "value": 1},
    ]

    def _save(self, client: TestClient, name: str) -> None:
        """Save the steps as a named mapping.

        Args:
            client: The authenticated client.
            name: The mapping's name.
        """
        response = client.post(
            "/api/v1/mappings",
            json={"name": name, "stream": "widgets", "steps": self.STEPS},
        )
        assert response.status_code == 201, response.text

    def test_a_saved_mapping_shapes_the_records_a_run_loads(
        self,
        ui_client: TestClient,
        singer_pipeline: Path,
    ) -> None:
        """A mapping saved over HTTP changes what reaches the loader.

        Without the mapper in the middle this pipeline loads two records with
        `id` and `name`; the assertion below is only reachable if the real
        mapper understood the compiled stream map.
        """
        self._save(ui_client, "shape-widgets")

        started = ui_client.post(
            "/api/v1/runs",
            json={"blocks": ["fake-tap", "shape-widgets", "fake-target"]},
        )
        assert started.status_code == 202

        run = _await_run(ui_client, started.json()["run_id"])
        assert run["status"] == "success", run

        assert _rows(singer_pipeline) == [{"id": 2, "label": "beta"}]

    def test_the_preview_agrees_with_the_run(
        self,
        ui_client: TestClient,
        singer_pipeline: Path,
    ) -> None:
        """What the shaper showed is what the pipeline produces.

        The preview applies the steps in this process, in Python; the run
        applies them in the mapper, from a compiled stream map. Two
        implementations of the same steps that agree by construction on
        nothing, compared against each other on real records - a divergence
        here is the failure the whole feature would be judged by.
        """
        preview = ui_client.post(
            "/api/v1/plugins/extractors/fake-tap/preview",
            json={"stream": "widgets", "steps": self.STEPS},
        )
        assert preview.status_code == 200, preview.text

        self._save(ui_client, "agreeing-widgets")
        started = ui_client.post(
            "/api/v1/runs",
            json={"blocks": ["fake-tap", "agreeing-widgets", "fake-target"]},
        )
        run = _await_run(ui_client, started.json()["run_id"])
        assert run["status"] == "success", run

        assert _rows(singer_pipeline) == preview.json()["rows"]


#: Emits one stream per conformance case, each carrying the same records, so
#: a single run can exercise the whole table: a mapping's `stream_maps` is
#: keyed by stream, so one mapping can hold every case's compiled map at once.
_CASE_TAP_SOURCE = '''\
"""A Singer tap emitting one stream per conformance case."""

import json
import os

cases = json.loads(os.environ["CONFORMANCE_CASES"])
records = json.loads(os.environ["CONFORMANCE_RECORDS"])

# Every property is declared nullable and untyped where it can be, because
# these streams carry the same records through different transformations and
# the mapper rewrites the schema from the expressions it is given.
properties = {
    key: {"type": ["null", "integer", "number", "string", "boolean"]}
    for key in records[0]
}

for stream in cases:
    print(
        json.dumps(
            {
                "type": "SCHEMA",
                "stream": stream,
                "key_properties": [],
                "schema": {"type": "object", "properties": properties},
            },
        ),
    )
    for record in records:
        print(
            json.dumps({"type": "RECORD", "stream": stream, "record": record}),
        )
'''

#: Writes each record with the stream it belongs to, which is how the rows of
#: one case are told from another's.
_CASE_TARGET_SOURCE = '''\
"""A Singer target recording which stream each record arrived on."""

import json
import os
import sys

with open(os.environ["CONFORMANCE_OUT"], "w", encoding="utf-8") as handle:
    for line in sys.stdin:
        message = json.loads(line)
        if message["type"] == "RECORD":
            handle.write(
                json.dumps(
                    {"stream": message["stream"], "record": message["record"]},
                )
                + "\\n",
            )
'''


def _stream_of(case: Case) -> str:
    """Return the stream name carrying one case.

    Args:
        case: The conformance case.

    Returns:
        A stream name safe to use as an identifier.
    """
    return "case_" + case.name.replace("-", "_")


@pytest.fixture
def conformance_pipeline(
    project: Project,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    """Declare a tap and loader carrying every conformance case.

    Args:
        project: The test project.
        tmp_path: Scratch space for the scripts and the loader's output.
        monkeypatch: Supplies the cases and records to the run subprocess.

    Yields:
        The file the loader writes to.
    """
    out = tmp_path / "conformance.jsonl"
    monkeypatch.setenv("CONFORMANCE_OUT", str(out))
    monkeypatch.setenv(
        "CONFORMANCE_CASES",
        json.dumps([_stream_of(case) for case in CASES]),
    )
    monkeypatch.setenv("CONFORMANCE_RECORDS", json.dumps(CASE_RECORDS))

    added = [
        _plugin(
            "extractors",
            "case-tap",
            _write_executable(tmp_path / "case-tap", _CASE_TAP_SOURCE),
        ),
        _plugin(
            "loaders",
            "case-target",
            _write_executable(tmp_path / "case-target", _CASE_TARGET_SOURCE),
        ),
    ]
    for plugin in added:
        project.plugins.add_to_file(plugin)

    try:
        yield out
    finally:
        for plugin in added:
            project.plugins.remove_from_file(plugin)


@pytest.mark.slow
@pytest.mark.usefixtures("conformance_pipeline", "real_mapper")
class TestTransformConformance:
    """Every case in the table, replayed through the real mapper.

    The preview applies steps sequentially to an evolving row; the compiled
    stream map is one declarative pass in which every expression reads the
    original record. Nothing makes those two agree by construction, and the
    shaper's whole promise is that they do.
    """

    def test_the_mapper_reproduces_every_previewed_result(
        self,
        ui_client: TestClient,
        project: Project,
        conformance_pipeline: Path,
    ) -> None:
        """One run, one stream per case, compared row for row.

        Cases share a run because a mapping's `stream_maps` is keyed by
        stream: one mapping can carry the whole table, which keeps this to a
        single pipeline rather than one per case.
        """
        stream_maps = {_stream_of(case): compile_steps(case.steps) for case in CASES}
        _put_mapping(project, "conformance", stream_maps)

        started = ui_client.post(
            "/api/v1/runs",
            json={"blocks": ["case-tap", "conformance", "case-target"]},
        )
        assert started.status_code == 202, started.text

        run = _await_run(ui_client, started.json()["run_id"])
        assert run["status"] == "success", run

        produced: dict[str, list[dict[str, t.Any]]] = {}
        for line in conformance_pipeline.read_text().splitlines():
            if not line:
                continue
            message = json.loads(line)
            produced.setdefault(message["stream"], []).append(message["record"])

        divergent = {
            case.name: {
                "expected": case.rows,
                "mapper": produced.get(_stream_of(case), []),
            }
            for case in CASES
            if produced.get(_stream_of(case), []) != case.rows
        }

        assert not divergent, (
            "the real mapper disagreed with the preview for: "
            f"{sorted(divergent)}\n{json.dumps(divergent, indent=2, default=str)}"
        )


def _put_mapping(
    project: Project,
    name: str,
    stream_maps: dict[str, t.Any],
) -> None:
    """Store one mapping carrying every case's stream map.

    Written straight to the file rather than through `POST /mappings`, which
    takes one stream per call: this needs them in a single mapping so that one
    run covers the table.

    Args:
        project: The test project.
        name: The mapping's name.
        stream_maps: Stream name to compiled stream map.
    """
    with project.config_service.update_meltano_yml() as meltano_yml:
        for plugin in meltano_yml["plugins"]["mappers"]:
            if not plugin.is_mapping():
                plugin.extras["mappings"] = [
                    {"name": name, "config": {"stream_maps": stream_maps}},
                ]
                return
