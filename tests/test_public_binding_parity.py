from __future__ import annotations

import argparse
import dataclasses
import importlib
import inspect
import json
import os
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Generator, get_args, get_type_hints

import pytest


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "contracts" / "public" / "operations.v2.json"
TS_BINDINGS_PATH = ROOT / "sdk" / "ts" / "src" / "generated" / "public-bindings.ts"
TS_ROUTES_PATH = ROOT / "sdk" / "ts" / "src" / "generated" / "routes.ts"
TS_CLIENT_PATH = ROOT / "sdk" / "ts" / "src" / "client.ts"
TS_INDEX_PATH = ROOT / "sdk" / "ts" / "src" / "index.ts"
TS_TYPES_PATH = ROOT / "sdk" / "ts" / "src" / "types.ts"
OPENAPI_PATH = ROOT / "docs" / "contracts" / "cli_bridge" / "openapi.json"
TUI_MANIFEST_PATH = (
    ROOT / "tui_skeleton" / "src" / "generated" / "public_surface_manifest.v1.json"
)
TYPESCRIPT_PATH = (
    ROOT / "sdk" / "ts" / "node_modules" / "typescript" / "lib" / "typescript.js"
)


# This helper uses the TypeScript compiler's parser rather than source-text
# matching. The generated binding and route files contain no imports, so the
# compiler's CommonJS output can also be evaluated directly for their values.
_TS_SNAPSHOT_SCRIPT = r"""
const fs = require("node:fs");
const ts = require(process.argv[1]);
const [bindingsPath, routesPath, clientPath, indexPath, typesPath] = process.argv.slice(2);

function sourceFile(path) {
  return ts.createSourceFile(
    path,
    fs.readFileSync(path, "utf8"),
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TS,
  );
}

function evaluate(path) {
  const output = ts.transpileModule(fs.readFileSync(path, "utf8"), {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText;
  const module = { exports: {} };
  new Function("module", "exports", output)(module, module.exports);
  return module.exports;
}

function nodeName(node, file) {
  if (!node || !node.name) return null;
  if (node.name.text !== undefined) return node.name.text;
  return node.name.getText(file);
}

function dtoShapes(path) {
  const file = sourceFile(path);
  const wanted = new Set([
    "Problem",
    "StageOutcome",
    "PublicResult",
    "PublicHarnessCreateRequest",
    "PublicHarnessUpdateRequest",
    "PublicSessionStartRequest",
    "PublicSessionInputRequest",
    "PublicSessionApprovalRequest",
    "PublicSessionCancelRequest",
  ]);
  const shapes = {};
  for (const statement of file.statements) {
    if (!ts.isInterfaceDeclaration(statement) || !wanted.has(statement.name.text)) {
      continue;
    }
    const properties = statement.members
      .filter((member) => ts.isPropertySignature(member))
      .map((member) => ({
        name: nodeName(member, file),
        required: member.questionToken === undefined,
      }));
    shapes[statement.name.text] = {
      properties: properties.map((property) => property.name),
      required: properties.filter((property) => property.required).map((property) => property.name),
    };
  }
  return shapes;
}

function stringUnionValues(path, typeName) {
  const file = sourceFile(path);
  for (const statement of file.statements) {
    if (
      ts.isTypeAliasDeclaration(statement)
      && statement.name.text === typeName
      && ts.isUnionTypeNode(statement.type)
    ) {
      return statement.type.types.map((member) => {
        if (!ts.isLiteralTypeNode(member) || !ts.isStringLiteralLike(member.literal)) {
          throw new Error(`${typeName} must contain only string literals`);
        }
        return member.literal.text;
      });
    }
  }
  throw new Error(`missing string union ${typeName}`);
}

function clientShape(path) {
  const file = sourceFile(path);
  const interfaceMethods = [];
  const factoryMethods = [];
  const actionBindings = [];
  const breadboardClientAssertions = [];

  function actionIds(node) {
    const ids = [];
    function visitAction(candidate) {
      if (
        ts.isCallExpression(candidate)
        && candidate.expression.getText(file) === "action"
        && candidate.arguments.length >= 2
        && ts.isStringLiteralLike(candidate.arguments[1])
      ) {
        ids.push(candidate.arguments[1].text);
      }
      ts.forEachChild(candidate, visitAction);
    }
    visitAction(node);
    return ids;
  }


  function visit(node) {
    if (
      (ts.isAsExpression(node) || ts.isTypeAssertionExpression(node))
      && node.type.getText(file) === "BreadboardClient"
    ) {
      breadboardClientAssertions.push(node.getText(file));
    }
    if (ts.isInterfaceDeclaration(node) && node.name.text === "BreadboardClient") {
      for (const member of node.members) {
        const name = nodeName(member, file);
        if (name !== null) interfaceMethods.push(name);
      }
    }
    if (
      ts.isVariableDeclaration(node)
      && node.name.getText(file) === "c"
      && node.initializer
      && ts.isObjectLiteralExpression(node.initializer)
    ) {
      for (const property of node.initializer.properties) {
        const name = nodeName(property, file);
        if (name === null) continue;
        factoryMethods.push(name);
        for (const id of actionIds(property)) {
          actionBindings.push({ method: name, actionId: id });
        }
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(file);
  return { interfaceMethods, factoryMethods, actionBindings, breadboardClientAssertions };
}

function rootExports(path) {
  const file = sourceFile(path);
  const named = [];
  const wildcards = [];
  for (const statement of file.statements) {
    if (!ts.isExportDeclaration(statement)) continue;
    const moduleSpecifier = statement.moduleSpecifier && statement.moduleSpecifier.text;
    if (statement.exportClause && ts.isNamedExports(statement.exportClause)) {
      for (const element of statement.exportClause.elements) named.push(element.name.text);
    } else {
      wildcards.push(moduleSpecifier || null);
    }
  }
  return { named, wildcards };
}

const bindings = evaluate(bindingsPath);
const routes = evaluate(routesPath);
process.stdout.write(JSON.stringify({
  bindings: {
    rows: bindings.PUBLIC_OPERATION_BINDINGS,
    byOperationId: bindings.PUBLIC_BINDINGS_BY_OPERATION_ID,
    byActionId: bindings.PUBLIC_BINDINGS_BY_ACTION_ID,
    routes: bindings.PUBLIC_ROUTES,
  },
  fullRoutes: routes.ROUTES,
  client: clientShape(clientPath),
  root: rootExports(indexPath),
  types: dtoShapes(typesPath),
  sessionDecisions: stringUnionValues(typesPath, "PublicSessionDecision"),
}));
"""


def _catalog() -> dict[str, Any]:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def _expected_rows() -> list[dict[str, Any]]:
    catalog = _catalog()
    operations = catalog["operations"]
    assert catalog["contract_id"] == "bb.public_operation_catalog.v2"
    assert len(operations) == 26
    operation_ids = [operation["operation_id"] for operation in operations]
    assert len(operation_ids) == len(set(operation_ids))
    rows: list[dict[str, Any]] = []
    for operation in operations:
        bindings = operation["bindings"]
        rows.append(
            {
                "operation_id": operation["operation_id"],
                "status": operation["status"],
                "http_method": bindings["openapi"]["method"],
                "path": bindings["openapi"]["path"],
                "cli_command": bindings["bbh"]["command"],
                "python_client": bindings["python_sdk"]["client"],
                "python_method": bindings["python_sdk"]["method"],
                "typescript_client": bindings["typescript_sdk"]["client"],
                "typescript_method": bindings["typescript_sdk"]["method"],
                "action_id": bindings["tui"]["action_id"],
                "action_kind": bindings["tui"]["kind"],
                "lifecycle": operation["lifecycle"],
                "idempotency_mode": operation["idempotency"]["mode"],
                "auth_mode": operation["auth_policy"]["mode"],
                "required_capabilities": tuple(
                    sorted(operation["required_capabilities"])
                ),
            }
        )
    return sorted(rows, key=lambda row: row["operation_id"])


def _binding_dict(value: Any) -> dict[str, Any]:
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if isinstance(value, Mapping):
        return dict(value)
    fields = getattr(value, "__dataclass_fields__", None)
    if fields is not None:
        return {name: getattr(value, name) for name in fields}
    raise TypeError(f"unsupported generated binding value: {type(value)!r}")


def _rows(value: Any) -> list[dict[str, Any]]:
    return sorted(
        (_binding_dict(row) for row in value), key=lambda row: row["operation_id"]
    )


def _route_tuple(route: Mapping[str, Any]) -> tuple[str, str, str]:
    return (str(route["operationId"]), str(route["method"]), str(route["path"]))


def _expected_public_routes(
    rows: list[dict[str, Any]],
) -> set[tuple[str, str, str]]:
    return {(row["operation_id"], row["http_method"], row["path"]) for row in rows}


def _leaf_commands(parser: argparse.ArgumentParser) -> set[tuple[str, ...]]:
    result: set[tuple[str, ...]] = set()

    def visit(current: argparse.ArgumentParser, prefix: tuple[str, ...]) -> None:
        action = next(
            (
                candidate
                for candidate in current._actions
                if isinstance(candidate, argparse._SubParsersAction)
            ),
            None,
        )
        if action is None:
            result.add(prefix)
            return
        for name, child in action.choices.items():
            visit(child, prefix + (name,))

    visit(parser, ())
    return result


def _typescript_snapshot() -> dict[str, Any]:
    node = shutil.which("node")
    if node is None or not TYPESCRIPT_PATH.is_file():
        pytest.fail(
            "Node plus sdk/ts/node_modules/typescript is required for TS AST parity"
        )
    completed = subprocess.run(
        [
            node,
            "-e",
            _TS_SNAPSHOT_SCRIPT,
            str(TYPESCRIPT_PATH),
            str(TS_BINDINGS_PATH),
            str(TS_ROUTES_PATH),
            str(TS_CLIENT_PATH),
            str(TS_INDEX_PATH),
            str(TS_TYPES_PATH),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=os.environ.copy(),
    )
    if completed.returncode != 0:
        pytest.fail(f"TypeScript AST/runtime inspection failed:\n{completed.stderr}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        pytest.fail(f"TypeScript AST/runtime inspection returned invalid JSON: {error}")


def test_generated_python_product_and_sdk_bindings_match_catalog() -> None:
    expected = _expected_rows()
    product = importlib.import_module(
        "breadboard.product.operations.generated_bindings"
    )
    sdk = importlib.import_module("breadboard_sdk.generated.public_bindings")
    for module in (product, sdk):
        actual = _rows(module.PUBLIC_OPERATION_BINDINGS)
        assert actual == expected
        assert set(module.PUBLIC_BINDINGS_BY_OPERATION_ID) == {
            row["operation_id"] for row in expected
        }
        assert {
            key: _binding_dict(value)
            for key, value in module.PUBLIC_BINDINGS_BY_OPERATION_ID.items()
        } == {row["operation_id"]: row for row in expected}


def test_fastapi_public_routes_match_catalog(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("BREADBOARD_LEGACY_ROUTES", raising=False)
    monkeypatch.delenv("BREADBOARD_ENABLE_PUBLIC_API", raising=False)
    monkeypatch.delenv("BREADBOARD_ENABLE_E4_API", raising=False)
    monkeypatch.setenv("RAY_SCE_LOCAL_MODE", "1")
    from fastapi.routing import APIRoute

    from breadboard_engine.api.cli_bridge.app import create_app
    from breadboard_engine.api.cli_bridge.service import SessionService

    app = create_app(
        SessionService(state_root=tmp_path / "service"),
        include_atp_routes=False,
    )
    rows = _expected_rows()
    expected_routes = _expected_public_routes(rows)
    expected_ids = {row["operation_id"] for row in rows}
    api_routes = [route for route in app.routes if isinstance(route, APIRoute)]
    public_routes = [
        route for route in api_routes if route.operation_id in expected_ids
    ]
    assert len(api_routes) > len(public_routes) == 26
    observed: set[tuple[str, str, str]] = set()
    for route in public_routes:
        operation_id = route.operation_id
        assert operation_id is not None
        methods = {
            method.upper()
            for method in (route.methods or ())
            if method.upper() not in {"HEAD", "OPTIONS"}
        }
        assert len(methods) == 1
        observed.add((operation_id, next(iter(methods)), route.path_format))
    assert len(observed) == 26
    assert observed == expected_routes


def test_bbh_argparse_leaf_commands_match_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BREADBOARD_LEGACY_ROUTES", raising=False)
    monkeypatch.delenv("BREADBOARD_ENABLE_E4_API", raising=False)
    from scripts.breadboard_cli import build_parser

    expected: set[tuple[str, ...]] = set()
    for operation in _catalog()["operations"]:
        command = operation["bindings"]["bbh"]["command"]
        assert command.startswith("bbh ")
        expected.add(tuple(command.split()[1:]))
    assert _leaf_commands(build_parser()) == expected


def test_python_sdk_explicit_methods_match_catalog() -> None:
    from breadboard_sdk.client import BreadBoardClient
    from breadboard_sdk.types import PublicResult, SessionEvent

    rows = _expected_rows()
    expected = {row["python_method"] for row in rows}
    actual = {
        name
        for name, value in vars(BreadBoardClient).items()
        if not name.startswith("_") and inspect.isfunction(value)
    }
    assert actual == expected
    for row in rows:
        expected_return = (
            Generator[SessionEvent, None, int | None]
            if row["operation_id"] == "session.events"
            else PublicResult
        )
        assert (
            get_type_hints(getattr(BreadBoardClient, row["python_method"]))["return"]
            == expected_return
        )


def test_openapi_transport_components_match_authored_sdk_dtos() -> None:
    import breadboard_sdk.types as python_types

    openapi = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    snapshot = _typescript_snapshot()
    dto_components = {
        "Problem": ("Problem", python_types.Problem),
        "StageOutcome": ("StageOutcome", python_types.StageOutcome),
        "PublicResult": ("PublicResult", python_types.PublicResult),
        "PublicHarnessCreateRequest": (
            "HarnessCreateRequest",
            python_types.PublicHarnessCreateRequest,
        ),
        "PublicHarnessUpdateRequest": (
            "HarnessUpdateRequest",
            python_types.PublicHarnessUpdateRequest,
        ),
        "PublicSessionStartRequest": (
            "SessionStartRequest",
            python_types.PublicSessionStartRequest,
        ),
        "PublicSessionInputRequest": (
            "SessionInputRequest",
            python_types.PublicSessionInputRequest,
        ),
        "PublicSessionApprovalRequest": (
            "SessionApprovalRequest",
            python_types.PublicSessionApprovalRequest,
        ),
        "PublicSessionCancelRequest": (
            "SessionCancelRequest",
            python_types.PublicSessionCancelRequest,
        ),
    }
    components = openapi["components"]["schemas"]
    assert set(snapshot["types"]) == set(dto_components)
    for typescript_name, (component_name, python_type) in dto_components.items():
        component = components[component_name]
        expected_properties = set(component["properties"])
        expected_required = set(component.get("required", []))
        if typescript_name == "PublicResult":
            # The serialized public envelope always carries its defaulted identity.
            expected_required.add("schema_version")
        assert set(get_type_hints(python_type)) == expected_properties
        assert set(python_type.__required_keys__) == expected_required
        assert set(snapshot["types"][typescript_name]["properties"]) == (
            expected_properties
        )
        assert set(snapshot["types"][typescript_name]["required"]) == expected_required
    expected_decisions = set(
        components["SessionApprovalRequest"]["properties"]["decision"]["enum"]
    )
    assert set(get_args(python_types.PublicSessionDecision)) == expected_decisions
    assert set(snapshot["sessionDecisions"]) == expected_decisions

    request_components = {
        "harness.create": "HarnessCreateRequest",
        "harness.update": "HarnessUpdateRequest",
        "session.start": "SessionStartRequest",
        "session.send_input": "SessionInputRequest",
        "session.approve": "SessionApprovalRequest",
        "session.cancel": "SessionCancelRequest",
    }
    for operation in _catalog()["operations"]:
        binding = operation["bindings"]["openapi"]
        observed = openapi["paths"][binding["path"]][binding["method"].lower()]
        request = observed.get("requestBody")
        expected_request = request_components.get(operation["operation_id"])
        if expected_request is None:
            assert request is None
        else:
            assert request["content"]["application/json"]["schema"] == {
                "$ref": f"#/components/schemas/{expected_request}"
            }
        if operation["operation_id"] == "session.events":
            continue
        successes = [
            response
            for status, response in observed["responses"].items()
            if status.startswith("2")
        ]
        assert len(successes) == 1
        assert successes[0]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/PublicResult"
        }


def test_typescript_tables_wrappers_and_root_exports_match_contract() -> None:
    expected = _expected_rows()
    expected_by_operation = {row["operation_id"]: row for row in expected}
    expected_public_routes = _expected_public_routes(expected)
    snapshot = _typescript_snapshot()
    generated_rows = sorted(
        snapshot["bindings"]["rows"], key=lambda row: row["operationId"]
    )
    expected_ts_rows = [
        {
            "operationId": row["operation_id"],
            "status": row["status"],
            "httpMethod": row["http_method"],
            "path": row["path"],
            "cliCommand": row["cli_command"],
            "pythonClient": row["python_client"],
            "pythonMethod": row["python_method"],
            "typescriptClient": row["typescript_client"],
            "typescriptMethod": row["typescript_method"],
            "actionId": row["action_id"],
            "actionKind": row["action_kind"],
            "lifecycle": row["lifecycle"],
            "idempotencyMode": row["idempotency_mode"],
            "authMode": row["auth_mode"],
            "requiredCapabilities": list(row["required_capabilities"]),
        }
        for row in expected
    ]
    assert generated_rows == expected_ts_rows
    assert {
        key: value for key, value in snapshot["bindings"]["byOperationId"].items()
    } == {
        row["operation_id"]: ts_row for row, ts_row in zip(expected, expected_ts_rows)
    }
    assert {
        key: value for key, value in snapshot["bindings"]["byActionId"].items()
    } == {row["action_id"]: ts_row for row, ts_row in zip(expected, expected_ts_rows)}
    assert {
        _route_tuple(route) for route in snapshot["bindings"]["routes"]
    } == expected_public_routes
    assert len(snapshot["bindings"]["routes"]) == 26
    expected_methods = {row["typescript_method"] for row in expected}

    assert expected_methods <= set(snapshot["client"]["factoryMethods"])
    assert expected_methods <= set(snapshot["client"]["interfaceMethods"])
    assert len(snapshot["client"]["interfaceMethods"]) == len(
        set(snapshot["client"]["interfaceMethods"])
    )
    assert snapshot["client"]["breadboardClientAssertions"] == []
    action_bindings = snapshot["client"]["actionBindings"]
    assert len(action_bindings) == len(
        {(item["method"], item["actionId"]) for item in action_bindings}
    )
    expected_action_bindings = {
        row["typescript_method"]: row["action_id"]
        for row in expected
        if row["operation_id"] != "session.events"
    }
    assert {
        item["method"]: item["actionId"] for item in action_bindings
    } == expected_action_bindings

    assert snapshot["root"] == {
        "named": [
            "ApiError",
            "createBreadboardClient",
            "createApiClient",
            "BreadboardClientConfig",
            "BreadboardClient",
            "PublicActionId",
            "PublicResult",
            "streamSessionEvents",
            "openEventStream",
            "EventStreamOptions",
            "StreamConfig",
            "EventStreamHandlers",
            "EventStreamHandle",
            "OpenEventStreamOptions",
            "Problem",
            "PublicHarnessCreateRequest",
            "PublicHarnessUpdateRequest",
            "PublicSessionApprovalRequest",
            "PublicSessionCancelRequest",
            "PublicSessionDecision",
            "PublicSessionInputRequest",
            "PublicSessionStartRequest",
            "SessionEvent",
            "SessionEventVisibility",
            "StageOutcome",
        ],
        "wildcards": [],
    }

    full_routes = [_route_tuple(route) for route in snapshot["fullRoutes"]]
    full_route_set = set(full_routes)
    assert len(full_routes) == len(full_route_set) == 51
    assert expected_public_routes <= full_route_set
    assert len(full_route_set - expected_public_routes) > 0
    assert any(
        operation_id not in expected_by_operation
        for operation_id, _, _ in full_route_set
    )


def test_tui_manifest_actions_match_catalog() -> None:
    manifest = json.loads(TUI_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["catalog_id"] == "bb.public_operation_catalog.v2"
    expected = sorted(
        {
            (
                row["operation_id"],
                row["action_id"],
                row["action_kind"],
            )
            for row in _expected_rows()
        }
    )
    operations = manifest["operations"]
    actual = sorted(
        (
            operation["operation_id"],
            operation["action_id"],
            operation["kind"],
        )
        for operation in operations
    )
    assert len(operations) == 26
    assert actual == expected


__all__ = []
