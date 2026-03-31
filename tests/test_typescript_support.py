"""Tests for TypeScript/Effect-TS extraction and smoke test generation."""

from __future__ import annotations

import textwrap

import pytest

from pact.codebase_analyzer import (
    analyze_codebase,
    discover_source_files,
    discover_tests,
    extract_functions_typescript,
)
from pact.adopt import (
    build_decomposition_tree,
    generate_smoke_tests,
)
from pact.schemas_testgen import (
    CodebaseAnalysis,
    ExtractedFunction,
    SourceFile,
)


# ── File Discovery ─────────────────────────────────────────────────


class TestTypeScriptFileDiscovery:
    def test_finds_ts_files(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "index.ts").write_text("export const x = 1")
        (tmp_path / "src" / "utils.ts").write_text("export const y = 2")
        files = discover_source_files(tmp_path, language="typescript")
        paths = [f.path for f in files]
        assert "src/index.ts" in paths
        assert "src/utils.ts" in paths

    def test_skips_test_files(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.ts").write_text("export const x = 1")
        (tmp_path / "src" / "main.test.ts").write_text("test('x', () => {})")
        (tmp_path / "src" / "main.spec.ts").write_text("test('x', () => {})")
        files = discover_source_files(tmp_path, language="typescript")
        paths = [f.path for f in files]
        assert "src/main.ts" in paths
        assert "src/main.test.ts" not in paths
        assert "src/main.spec.ts" not in paths

    def test_skips_declaration_files(self, tmp_path):
        (tmp_path / "types.d.ts").write_text("declare module 'foo' {}")
        (tmp_path / "index.ts").write_text("export const x = 1")
        files = discover_source_files(tmp_path, language="typescript")
        paths = [f.path for f in files]
        assert "index.ts" in paths
        assert "types.d.ts" not in paths

    def test_skips_node_modules(self, tmp_path):
        (tmp_path / "node_modules" / "effect").mkdir(parents=True)
        (tmp_path / "node_modules" / "effect" / "index.ts").write_text("x = 1")
        (tmp_path / "index.ts").write_text("export const x = 1")
        files = discover_source_files(tmp_path, language="typescript")
        paths = [f.path for f in files]
        assert not any("node_modules" in p for p in paths)

    def test_discovers_test_files(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "foo.test.ts").write_text("test('x', () => {})")
        (tmp_path / "src" / "bar.spec.ts").write_text("it('y', () => {})")
        (tmp_path / "__tests__").mkdir()
        (tmp_path / "__tests__" / "baz.ts").write_text("test('z', () => {})")
        tfiles = discover_tests(tmp_path, language="typescript")
        paths = [f.path for f in tfiles]
        assert "src/foo.test.ts" in paths
        assert "src/bar.spec.ts" in paths
        assert "__tests__/baz.ts" in paths


# ── Function Extraction: Standard TypeScript ────────────────────────


class TestTypeScriptFunctionExtraction:
    def test_function_declaration(self):
        source = 'export function greet(name: string): string { return `Hello ${name}` }'
        funcs = extract_functions_typescript("test.ts", source)
        fns = [f for f in funcs if f.name == "greet"]
        assert len(fns) == 1
        f = fns[0]
        assert f.name == "greet"
        assert "export" in f.decorators
        assert len(f.params) == 1
        assert f.params[0].name == "name"
        assert f.params[0].type_annotation == "string"
        assert f.return_type == "string"

    def test_async_function(self):
        source = "export async function fetchData(url: string): Promise<Response> { return fetch(url) }"
        funcs = extract_functions_typescript("test.ts", source)
        fns = [f for f in funcs if f.name == "fetchData"]
        assert len(fns) == 1
        assert fns[0].is_async is True
        assert fns[0].return_type == "Promise<Response>"

    def test_non_exported_function(self):
        source = "function helper(x: number): number { return x + 1 }"
        funcs = extract_functions_typescript("test.ts", source)
        fns = [f for f in funcs if f.name == "helper"]
        assert len(fns) == 1
        assert "export" not in fns[0].decorators

    def test_arrow_function(self):
        source = "export const add = (a: number, b: number): number => a + b"
        funcs = extract_functions_typescript("test.ts", source)
        fns = [f for f in funcs if f.name == "add"]
        assert len(fns) == 1
        f = fns[0]
        assert "export" in f.decorators
        assert len(f.params) == 2
        assert f.params[0].name == "a"
        assert f.params[1].name == "b"

    def test_async_arrow_function(self):
        source = "export const fetchItems = async (ids: string[]): Promise<Item[]> => { /* ... */ }"
        funcs = extract_functions_typescript("test.ts", source)
        fns = [f for f in funcs if f.name == "fetchItems"]
        assert len(fns) == 1
        assert fns[0].is_async is True

    def test_class_declaration(self):
        source = "export class UserService { constructor(private db: Database) {} }"
        funcs = extract_functions_typescript("test.ts", source)
        fns = [f for f in funcs if f.name == "UserService"]
        assert len(fns) == 1
        assert "class" in fns[0].decorators
        assert fns[0].return_type == "class"

    def test_interface_declaration(self):
        source = "export interface UserConfig { name: string; age: number }"
        funcs = extract_functions_typescript("test.ts", source)
        fns = [f for f in funcs if f.name == "UserConfig"]
        assert len(fns) == 1
        assert "interface" in fns[0].decorators

    def test_type_alias(self):
        source = "export type UserId = string & { readonly _brand: 'UserId' }"
        funcs = extract_functions_typescript("test.ts", source)
        fns = [f for f in funcs if f.name == "UserId"]
        assert len(fns) == 1
        assert "type" in fns[0].decorators

    def test_multiple_functions(self):
        source = textwrap.dedent("""\
            export function a(): void {}
            export function b(): void {}
            export const c = () => {}
        """)
        funcs = extract_functions_typescript("test.ts", source)
        names = [f.name for f in funcs]
        assert "a" in names
        assert "b" in names
        assert "c" in names

    def test_generator_function(self):
        source = "export function* items(): Generator<number> { yield 1 }"
        funcs = extract_functions_typescript("test.ts", source)
        fns = [f for f in funcs if f.name == "items"]
        assert len(fns) == 1
        assert "generator" in fns[0].decorators

    def test_optional_and_default_params(self):
        source = 'export function init(name: string, debug?: boolean, level: number = 1): void {}'
        funcs = extract_functions_typescript("test.ts", source)
        fns = [f for f in funcs if f.name == "init"]
        assert len(fns) == 1
        assert len(fns[0].params) == 3
        assert fns[0].params[2].default == "1"

    def test_line_numbers(self):
        source = "\n\nexport function third(): void {}"
        funcs = extract_functions_typescript("test.ts", source)
        fns = [f for f in funcs if f.name == "third"]
        assert fns[0].line_number == 3

    def test_generic_function(self):
        source = "export function identity<T>(value: T): T { return value }"
        funcs = extract_functions_typescript("test.ts", source)
        fns = [f for f in funcs if f.name == "identity"]
        assert len(fns) == 1
        assert fns[0].return_type == "T"


# ── Function Extraction: Effect-TS Patterns ────────────────────────


class TestEffectTSExtraction:
    def test_effect_gen(self):
        source = textwrap.dedent("""\
            export const getUser = Effect.gen(function*() {
              const repo = yield* UserRepo
              return yield* repo.getById("123")
            })
        """)
        funcs = extract_functions_typescript("test.ts", source)
        fns = [f for f in funcs if f.name == "getUser"]
        assert len(fns) == 1
        f = fns[0]
        assert "effect_gen" in f.decorators
        assert "export" in f.decorators
        assert f.is_async is True  # Effect.gen is async-like

    def test_effect_gen_with_type_annotation(self):
        source = textwrap.dedent("""\
            export const getUser: Effect.Effect<User, UserNotFound, UserRepo> = Effect.gen(function*() {
              const repo = yield* UserRepo
              return yield* repo.getById("123")
            })
        """)
        funcs = extract_functions_typescript("test.ts", source)
        fns = [f for f in funcs if f.name == "getUser"]
        assert len(fns) == 1
        # Should extract the type annotation as return type
        assert "Effect.Effect<User, UserNotFound, UserRepo>" in fns[0].return_type

    def test_pipe_composition(self):
        source = textwrap.dedent("""\
            export const processUser = pipe(
              getUser,
              Effect.flatMap(validate),
              Effect.catchTag("UserNotFound", handleNotFound)
            )
        """)
        funcs = extract_functions_typescript("test.ts", source)
        fns = [f for f in funcs if f.name == "processUser"]
        assert len(fns) == 1
        assert "pipe" in fns[0].decorators

    def test_layer_definition(self):
        source = textwrap.dedent("""\
            export const UserRepoLive = Layer.succeed(
              UserRepo,
              { getById: (id: string) => Effect.succeed({ id, name: "test" }) }
            )
        """)
        funcs = extract_functions_typescript("test.ts", source)
        fns = [f for f in funcs if f.name == "UserRepoLive"]
        assert len(fns) == 1
        assert "layer" in fns[0].decorators

    def test_layer_effect(self):
        source = "export const DbLive = Layer.effect(Database, Effect.gen(function*() { /* ... */ }))"
        funcs = extract_functions_typescript("test.ts", source)
        fns = [f for f in funcs if f.name == "DbLive"]
        assert len(fns) == 1
        assert "layer" in fns[0].decorators

    def test_layer_scoped(self):
        source = "export const PoolLive = Layer.scoped(Pool, Effect.gen(function*() { /* ... */ }))"
        funcs = extract_functions_typescript("test.ts", source)
        fns = [f for f in funcs if f.name == "PoolLive"]
        assert len(fns) == 1
        assert "layer" in fns[0].decorators

    def test_context_tag_service(self):
        source = textwrap.dedent("""\
            export class UserRepo extends Context.Tag("UserRepo")<
              UserRepo,
              { getById: (id: string) => Effect.Effect<User, UserNotFound> }
            >() {}
        """)
        funcs = extract_functions_typescript("test.ts", source)
        fns = [f for f in funcs if f.name == "UserRepo"]
        assert len(fns) == 1
        assert "service" in fns[0].decorators
        assert fns[0].return_type == "service"

    def test_data_tagged_error(self):
        source = textwrap.dedent("""\
            export class UserNotFound extends Data.TaggedError("UserNotFound")<{
              id: string
            }>() {}
        """)
        funcs = extract_functions_typescript("test.ts", source)
        fns = [f for f in funcs if f.name == "UserNotFound"]
        assert len(fns) == 1
        assert "tagged_error" in fns[0].decorators

    def test_schema_struct(self):
        source = textwrap.dedent("""\
            export const User = Schema.Struct({
              id: Schema.String,
              name: Schema.String,
              age: Schema.Number,
            })
        """)
        funcs = extract_functions_typescript("test.ts", source)
        fns = [f for f in funcs if f.name == "User"]
        assert len(fns) == 1
        assert "schema" in fns[0].decorators

    def test_schema_class(self):
        source = "export const User = Schema.Class<User>('User')({ id: Schema.String, name: Schema.String })"
        funcs = extract_functions_typescript("test.ts", source)
        fns = [f for f in funcs if f.name == "User"]
        assert len(fns) == 1
        assert "schema" in fns[0].decorators

    def test_arrow_returning_effect(self):
        source = textwrap.dedent("""\
            export const getUser = (id: string): Effect.Effect<User, UserNotFound, UserRepo> =>
              Effect.gen(function*() {
                const repo = yield* UserRepo
                return yield* repo.getById(id)
              })
        """)
        funcs = extract_functions_typescript("test.ts", source)
        fns = [f for f in funcs if f.name == "getUser"]
        assert len(fns) == 1
        f = fns[0]
        # Should be detected as arrow function with Effect return
        assert len(f.params) == 1
        assert f.params[0].name == "id"

    def test_mixed_effect_file(self):
        """A realistic Effect-TS module with multiple patterns."""
        source = textwrap.dedent("""\
            import { Effect, Context, Layer, Data, Schema, pipe } from "effect"

            // Service definition
            export class UserRepo extends Context.Tag("UserRepo")<
              UserRepo,
              { getById: (id: string) => Effect.Effect<User, UserNotFound> }
            >() {}

            // Error type
            export class UserNotFound extends Data.TaggedError("UserNotFound")<{
              id: string
            }>() {}

            // Schema
            export const User = Schema.Struct({
              id: Schema.String,
              name: Schema.String,
            })

            // Effect.gen function
            export const getUser = Effect.gen(function*() {
              const repo = yield* UserRepo
              return yield* repo.getById("123")
            })

            // Layer
            export const UserRepoLive = Layer.succeed(UserRepo, {
              getById: (id) => Effect.succeed({ id, name: "test" }),
            })

            // Pipe composition
            export const program = pipe(
              getUser,
              Effect.tap(Effect.log),
            )

            // Regular function
            export function createApp(): void {}
        """)
        funcs = extract_functions_typescript("test.ts", source)
        names = {f.name for f in funcs}

        assert "UserRepo" in names
        assert "UserNotFound" in names
        assert "User" in names
        assert "getUser" in names
        assert "UserRepoLive" in names
        assert "program" in names
        assert "createApp" in names

        # Check specific types
        by_name = {f.name: f for f in funcs}
        assert "service" in by_name["UserRepo"].decorators
        assert "tagged_error" in by_name["UserNotFound"].decorators
        assert "schema" in by_name["User"].decorators
        assert "effect_gen" in by_name["getUser"].decorators
        assert "layer" in by_name["UserRepoLive"].decorators
        assert "pipe" in by_name["program"].decorators

    def test_plain_const_not_extracted(self):
        """Plain constants that aren't function-like should be skipped."""
        source = textwrap.dedent("""\
            export const MAX_RETRIES = 3
            export const DEFAULT_TIMEOUT = 5000
            export const CONFIG = { debug: false }
        """)
        funcs = extract_functions_typescript("test.ts", source)
        names = {f.name for f in funcs}
        assert "MAX_RETRIES" not in names
        assert "DEFAULT_TIMEOUT" not in names
        assert "CONFIG" not in names


# ── Smoke Test Generation ─────────────────────────────────────────


class TestTypeScriptSmokeTests:
    def test_generates_vitest_tests(self):
        analysis = CodebaseAnalysis(
            root_path="/tmp/test",
            language="typescript",
            source_files=[
                SourceFile(path="src/utils.ts", functions=[
                    ExtractedFunction(name="add", decorators=["export"]),
                    ExtractedFunction(name="multiply", decorators=["export"]),
                ]),
            ],
        )
        suites = generate_smoke_tests(analysis, language="typescript")
        assert len(suites) == 1
        key = list(suites.keys())[0]
        assert key.endswith(".test.ts")
        code = suites[key]
        assert "import { describe, it, expect } from 'vitest'" in code
        assert "exports add" in code
        assert "exports multiply" in code
        assert "typeof mod.add" in code

    def test_skips_private_functions(self):
        analysis = CodebaseAnalysis(
            root_path="/tmp/test",
            language="typescript",
            source_files=[
                SourceFile(path="src/utils.ts", functions=[
                    ExtractedFunction(name="publicFn", decorators=["export"]),
                    ExtractedFunction(name="_privateFn", decorators=["export"]),
                ]),
            ],
        )
        suites = generate_smoke_tests(analysis, language="typescript")
        code = list(suites.values())[0]
        assert "publicFn" in code
        assert "_privateFn" not in code

    def test_effect_exports_use_defined_check(self):
        """Effect-TS values (layers, schemas) should use toBeDefined, not typeof function."""
        analysis = CodebaseAnalysis(
            root_path="/tmp/test",
            language="typescript",
            source_files=[
                SourceFile(path="src/services.ts", functions=[
                    ExtractedFunction(name="UserRepoLive", decorators=["export", "layer"]),
                    ExtractedFunction(name="User", decorators=["export", "schema"]),
                    ExtractedFunction(name="getUser", decorators=["export", "effect_gen"]),
                ]),
            ],
        )
        suites = generate_smoke_tests(analysis, language="typescript")
        code = list(suites.values())[0]
        # Layer, schema, effect_gen should NOT have typeof function check
        assert "typeof mod.UserRepoLive" not in code
        assert "typeof mod.User" not in code
        assert "typeof mod.getUser" not in code
        # But all should have toBeDefined
        assert "mod.UserRepoLive" in code
        assert "mod.User" in code
        assert "mod.getUser" in code

    def test_skips_unexported(self):
        analysis = CodebaseAnalysis(
            root_path="/tmp/test",
            language="typescript",
            source_files=[
                SourceFile(path="src/utils.ts", functions=[
                    ExtractedFunction(name="exported", decorators=["export"]),
                    ExtractedFunction(name="internal", decorators=[]),
                ]),
            ],
        )
        suites = generate_smoke_tests(analysis, language="typescript")
        code = list(suites.values())[0]
        assert "exported" in code
        # internal should only appear in the module import test, not as its own export test
        assert "exports internal" not in code

    def test_empty_codebase(self):
        analysis = CodebaseAnalysis(
            root_path="/tmp/test",
            language="typescript",
            source_files=[],
        )
        suites = generate_smoke_tests(analysis, language="typescript")
        assert suites == {}


# ── Tree Construction (TS extension handling) ─────────────────────


class TestTreeConstructionTS:
    def test_component_id_strips_ts(self):
        analysis = CodebaseAnalysis(
            root_path="/tmp/test",
            language="typescript",
            source_files=[
                SourceFile(path="src/auth/login.ts", functions=[
                    ExtractedFunction(name="authenticate"),
                ]),
            ],
        )
        tree = build_decomposition_tree(analysis)
        assert "src_auth_login" in tree.nodes

    def test_component_id_strips_js(self):
        analysis = CodebaseAnalysis(
            root_path="/tmp/test",
            language="javascript",
            source_files=[
                SourceFile(path="src/utils.js", functions=[
                    ExtractedFunction(name="helper"),
                ]),
            ],
        )
        tree = build_decomposition_tree(analysis)
        assert "src_utils" in tree.nodes


# ── Full Analysis Integration ──────────────────────────────────────


class TestAnalyzeCodebaseTS:
    def test_analyzes_typescript_project(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "index.ts").write_text(textwrap.dedent("""\
            import { Effect, Layer, Context, Schema, pipe } from "effect"

            export class UserRepo extends Context.Tag("UserRepo")<
              UserRepo,
              { getById: (id: string) => Effect.Effect<string> }
            >() {}

            export const getUser = Effect.gen(function*() {
              const repo = yield* UserRepo
              return yield* repo.getById("123")
            })

            export function healthCheck(): string {
              return "ok"
            }

            export const UserRepoLive = Layer.succeed(UserRepo, {
              getById: (id) => Effect.succeed(id),
            })
        """))

        analysis = analyze_codebase(tmp_path, language="typescript")
        assert analysis.total_source_files >= 1
        assert analysis.total_functions >= 3

        # Check specific extractions
        sf = analysis.source_files[0]
        names = {f.name for f in sf.functions}
        assert "UserRepo" in names
        assert "getUser" in names
        assert "healthCheck" in names
        assert "UserRepoLive" in names

    def test_empty_ts_project(self, tmp_path):
        analysis = analyze_codebase(tmp_path, language="typescript")
        assert analysis.total_functions == 0
        assert analysis.total_source_files == 0

    def test_discovers_ts_tests(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.ts").write_text("export function run(): void {}")
        (tmp_path / "src" / "app.test.ts").write_text(textwrap.dedent("""\
            import { run } from './app'
            import { describe, it, expect } from 'vitest'

            describe('app', () => {
              it('should run', () => {
                expect(run()).toBeUndefined()
              })
            })
        """))

        analysis = analyze_codebase(tmp_path, language="typescript")
        assert analysis.total_source_files >= 1
        assert analysis.total_test_files >= 1

        # Source should not include test files
        source_paths = [sf.path for sf in analysis.source_files]
        assert "src/app.ts" in source_paths
        assert "src/app.test.ts" not in source_paths


# ── Dry-Run Adoption Integration ──────────────────────────────────


class TestAdoptTypeScript:
    @pytest.mark.asyncio
    async def test_dry_run_ts_project(self, tmp_path):
        """Full dry-run adoption of a TypeScript Effect project."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.ts").write_text(textwrap.dedent("""\
            import { Effect, Context, Layer } from "effect"

            export class AppConfig extends Context.Tag("AppConfig")<
              AppConfig,
              { port: number; debug: boolean }
            >() {}

            export const startServer = Effect.gen(function*() {
              const config = yield* AppConfig
              console.log("starting on port", config.port)
            })

            export const AppConfigLive = Layer.succeed(AppConfig, {
              port: 3000,
              debug: false,
            })

            export function healthCheck(): string {
              return "ok"
            }
        """))

        from pact.adopt import adopt_codebase

        result = await adopt_codebase(tmp_path, language="typescript", dry_run=True)
        assert result.dry_run is True
        assert result.components >= 1
        assert result.total_functions >= 3  # AppConfig, startServer, AppConfigLive, healthCheck
        assert result.smoke_tests_generated >= 1

        # Check smoke test was generated
        smoke_dir = tmp_path / "tests" / "smoke"
        assert smoke_dir.exists()
        test_files = list(smoke_dir.glob("*.test.ts"))
        assert len(test_files) >= 1

        # Verify content has vitest imports
        code = test_files[0].read_text()
        assert "vitest" in code
        assert "healthCheck" in code
