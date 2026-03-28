"""Tests for Rust language support across Pact modules.

Covers:
  - codebase_analyzer: extract_functions_rust, Rust regex patterns, file discovery
  - interface_stub: render_stub_rust, _map_type_rust
  - test_harness: parse_cargo_test_output
  - adopt: _generate_rust_smoke_tests
  - ci: generate_rust_workflow
"""

from __future__ import annotations

import textwrap

import pytest

from pact.codebase_analyzer import (
    _RUST_ENUM_RE,
    _RUST_FN_RE,
    _RUST_IMPL_RE,
    _RUST_STRUCT_RE,
    _RUST_TEST_FN_RE,
    _RUST_TRAIT_RE,
    _extract_rust_referenced_names,
    _extract_rust_test_function_names,
    discover_source_files,
    discover_tests,
    extract_functions_rust,
)
from pact.interface_stub import (
    _map_type_rust,
    render_stub_rust,
)
from pact.test_harness import parse_cargo_test_output
from pact.adopt import _generate_rust_smoke_tests, generate_smoke_tests
from pact.ci import generate_rust_workflow
from pact.schemas import (
    ComponentContract,
    ErrorCase,
    FieldSpec,
    FunctionContract,
    TypeSpec,
    ValidatorSpec,
)
from pact.schemas_testgen import (
    CodebaseAnalysis,
    CoverageMap,
    ExtractedFunction,
    ExtractedParameter,
    SecurityAuditReport,
    SourceFile,
)


# ── Fixtures ──────────────────────────────────────────────────────────


SIMPLE_RUST_SOURCE = textwrap.dedent("""\
    use std::collections::HashMap;

    pub fn greet(name: &str) -> String {
        format!("Hello, {}!", name)
    }

    fn helper(x: i32, y: i32) -> i32 {
        x + y
    }

    pub async fn fetch_data(url: &str) -> Result<String, reqwest::Error> {
        reqwest::get(url).await?.text().await
    }
""")


COMPLEX_RUST_SOURCE = textwrap.dedent("""\
    use serde::{Deserialize, Serialize};

    /// A user in the system.
    #[derive(Debug, Clone, Serialize, Deserialize)]
    pub struct User {
        pub name: String,
        pub age: u32,
    }

    pub enum Status {
        Active,
        Inactive,
        Banned,
    }

    pub trait Authenticator {
        fn authenticate(&self, token: &str) -> bool;
    }

    impl User {
        pub fn new(name: String, age: u32) -> Self {
            Self { name, age }
        }

        pub fn greet(&self) -> String {
            format!("Hello, {}!", self.name)
        }
    }

    pub fn process_items<T: Clone>(items: &[T], count: usize) -> Vec<T> {
        items.iter().take(count).cloned().collect()
    }

    pub fn with_lifetime<'a>(data: &'a str) -> &'a str {
        data.trim()
    }

    pub(crate) fn internal_helper() -> bool {
        true
    }

    pub unsafe fn dangerous_op(ptr: *const u8) -> u8 {
        *ptr
    }
""")


RUST_TEST_SOURCE = textwrap.dedent("""\
    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn test_greet() {
            assert_eq!(greet("World"), "Hello, World!");
        }

        #[test]
        fn test_helper() {
            assert_eq!(helper(2, 3), 5);
        }

        #[test]
        #[should_panic]
        fn test_panic_case() {
            panic!("expected");
        }
    }
""")


# ── extract_functions_rust ────────────────────────────────────────────


class TestExtractFunctionsRust:
    """Tests for extract_functions_rust() in codebase_analyzer."""

    def test_extracts_pub_fn(self):
        funcs = extract_functions_rust("lib.rs", source=SIMPLE_RUST_SOURCE)
        names = [f.name for f in funcs]
        assert "greet" in names

    def test_extracts_private_fn(self):
        funcs = extract_functions_rust("lib.rs", source=SIMPLE_RUST_SOURCE)
        names = [f.name for f in funcs]
        assert "helper" in names

    def test_extracts_async_fn(self):
        funcs = extract_functions_rust("lib.rs", source=SIMPLE_RUST_SOURCE)
        async_funcs = [f for f in funcs if f.name == "fetch_data"]
        assert len(async_funcs) == 1
        assert async_funcs[0].is_async is True

    def test_marks_pub_in_decorators(self):
        funcs = extract_functions_rust("lib.rs", source=SIMPLE_RUST_SOURCE)
        greet = next(f for f in funcs if f.name == "greet")
        assert "pub" in greet.decorators

    def test_private_fn_no_pub_decorator(self):
        funcs = extract_functions_rust("lib.rs", source=SIMPLE_RUST_SOURCE)
        helper = next(f for f in funcs if f.name == "helper")
        assert "pub" not in helper.decorators

    def test_extracts_pub_struct(self):
        funcs = extract_functions_rust("lib.rs", source=COMPLEX_RUST_SOURCE)
        structs = [f for f in funcs if "struct" in f.decorators]
        struct_names = [f.name for f in structs]
        assert "User" in struct_names

    def test_struct_return_type_is_struct(self):
        funcs = extract_functions_rust("lib.rs", source=COMPLEX_RUST_SOURCE)
        user = next(f for f in funcs if f.name == "User" and "struct" in f.decorators)
        assert user.return_type == "struct"

    def test_extracts_pub_enum(self):
        funcs = extract_functions_rust("lib.rs", source=COMPLEX_RUST_SOURCE)
        enums = [f for f in funcs if "enum" in f.decorators]
        enum_names = [f.name for f in enums]
        assert "Status" in enum_names

    def test_extracts_pub_trait(self):
        funcs = extract_functions_rust("lib.rs", source=COMPLEX_RUST_SOURCE)
        traits = [f for f in funcs if "trait" in f.decorators]
        trait_names = [f.name for f in traits]
        assert "Authenticator" in trait_names

    def test_extracts_impl_blocks(self):
        funcs = extract_functions_rust("lib.rs", source=COMPLEX_RUST_SOURCE)
        impls = [f for f in funcs if "impl" in f.decorators]
        impl_names = [f.name for f in impls]
        assert "User" in impl_names

    def test_extracts_generic_fn_name(self):
        funcs = extract_functions_rust("lib.rs", source=COMPLEX_RUST_SOURCE)
        names = [f.name for f in funcs]
        assert "process_items" in names

    def test_generic_fn_params_not_extracted(self):
        """Generic fns match on '<' not '(' so params aren't extracted via regex."""
        funcs = extract_functions_rust("lib.rs", source=COMPLEX_RUST_SOURCE)
        process = next(f for f in funcs if f.name == "process_items")
        # The regex matches the < delimiter, so param extraction doesn't find (
        assert process.params == []

    def test_extracts_lifetime_fn(self):
        funcs = extract_functions_rust("lib.rs", source=COMPLEX_RUST_SOURCE)
        names = [f.name for f in funcs]
        assert "with_lifetime" in names

    def test_extracts_return_types(self):
        funcs = extract_functions_rust("lib.rs", source=SIMPLE_RUST_SOURCE)
        greet = next(f for f in funcs if f.name == "greet")
        assert "String" in greet.return_type

    def test_extracts_result_return_type(self):
        funcs = extract_functions_rust("lib.rs", source=SIMPLE_RUST_SOURCE)
        fetch = next(f for f in funcs if f.name == "fetch_data")
        assert "Result" in fetch.return_type

    def test_extracts_params_with_types(self):
        funcs = extract_functions_rust("lib.rs", source=SIMPLE_RUST_SOURCE)
        helper = next(f for f in funcs if f.name == "helper")
        param_types = [p.type_annotation for p in helper.params]
        assert "i32" in param_types

    def test_handles_self_parameter(self):
        funcs = extract_functions_rust("lib.rs", source=COMPLEX_RUST_SOURCE)
        greet = next(f for f in funcs if f.name == "greet" and f.is_method)
        assert greet.is_method is True

    def test_pub_crate_fn(self):
        funcs = extract_functions_rust("lib.rs", source=COMPLEX_RUST_SOURCE)
        names = [f.name for f in funcs]
        assert "internal_helper" in names

    def test_unsafe_fn(self):
        funcs = extract_functions_rust("lib.rs", source=COMPLEX_RUST_SOURCE)
        names = [f.name for f in funcs]
        assert "dangerous_op" in names

    def test_correct_line_numbers(self):
        funcs = extract_functions_rust("lib.rs", source=SIMPLE_RUST_SOURCE)
        greet = next(f for f in funcs if f.name == "greet")
        # greet is on line 3 of the source
        assert greet.line_number == 3

    def test_empty_source(self):
        funcs = extract_functions_rust("lib.rs", source="")
        assert funcs == []

    def test_comments_only(self):
        source = "// Just a comment\n/* block comment */\n"
        funcs = extract_functions_rust("lib.rs", source=source)
        assert funcs == []


# ── Rust Regex Patterns ───────────────────────────────────────────────


class TestRustRegexPatterns:
    """Tests for the raw regex patterns used for Rust parsing."""

    def test_fn_regex_matches_pub_fn(self):
        assert _RUST_FN_RE.search("pub fn foo(x: i32) -> bool {")

    def test_fn_regex_matches_async_fn(self):
        assert _RUST_FN_RE.search("pub async fn bar(url: &str) -> String {")

    def test_fn_regex_matches_generic_fn(self):
        m = _RUST_FN_RE.search("fn process<T: Clone>(items: &[T]) -> Vec<T> {")
        assert m
        assert m.group(1) == "process"

    def test_fn_regex_matches_pub_crate(self):
        m = _RUST_FN_RE.search("pub(crate) fn internal() {")
        assert m
        assert m.group(1) == "internal"

    def test_fn_regex_matches_unsafe(self):
        m = _RUST_FN_RE.search("pub unsafe fn danger(ptr: *const u8) -> u8 {")
        assert m
        assert m.group(1) == "danger"

    def test_struct_regex(self):
        m = _RUST_STRUCT_RE.search("pub struct Config {")
        assert m
        assert m.group(1) == "Config"

    def test_enum_regex(self):
        m = _RUST_ENUM_RE.search("pub enum Color {")
        assert m
        assert m.group(1) == "Color"

    def test_trait_regex(self):
        m = _RUST_TRAIT_RE.search("pub trait Handler {")
        assert m
        assert m.group(1) == "Handler"

    def test_impl_regex(self):
        m = _RUST_IMPL_RE.search("impl Config {")
        assert m
        assert m.group(1) == "Config"

    def test_impl_generic_regex(self):
        m = _RUST_IMPL_RE.search("impl<T: Clone> Container<T> {")
        assert m
        assert m.group(1) == "Container"

    def test_test_fn_regex(self):
        source = '#[test]\nfn test_add() {\n    assert_eq!(1 + 1, 2);\n}'
        matches = _RUST_TEST_FN_RE.findall(source)
        assert "test_add" in matches

    def test_test_fn_regex_with_attributes(self):
        source = '#[test]\n#[should_panic]\nfn test_panics() {\n    panic!();\n}'
        matches = _RUST_TEST_FN_RE.findall(source)
        assert "test_panics" in matches


# ── Rust Test Function & Reference Extraction ─────────────────────────


class TestRustTestHelpers:
    """Tests for _extract_rust_test_function_names and _extract_rust_referenced_names."""

    def test_extract_test_function_names(self):
        names = _extract_rust_test_function_names(RUST_TEST_SOURCE)
        assert "test_greet" in names
        assert "test_helper" in names
        assert "test_panic_case" in names

    def test_extract_referenced_names(self):
        names = _extract_rust_referenced_names(COMPLEX_RUST_SOURCE)
        # Should include function names found by _RUST_FN_RE
        assert "process_items" in names
        assert "with_lifetime" in names

    def test_extract_referenced_names_use_statements(self):
        source = "use std::collections::HashMap;\nuse crate::config::Config;\n"
        names = _extract_rust_referenced_names(source)
        assert "HashMap" in names
        assert "Config" in names


# ── File Discovery for Rust ───────────────────────────────────────────


class TestRustFileDiscovery:
    """Tests for discover_source_files and discover_tests with language='rust'."""

    def test_discovers_rs_files(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "lib.rs").write_text("pub fn hello() {}")
        (src / "utils.rs").write_text("fn help() {}")
        files = discover_source_files(tmp_path, language="rust")
        paths = [f.path for f in files]
        assert "src/lib.rs" in paths
        assert "src/utils.rs" in paths

    def test_skips_target_dir(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.rs").write_text("fn main() {}")
        target = tmp_path / "target"
        target.mkdir()
        debug = target / "debug"
        debug.mkdir()
        (debug / "deps.rs").write_text("// build artifact")
        files = discover_source_files(tmp_path, language="rust")
        paths = [f.path for f in files]
        assert not any("target" in p for p in paths)

    def test_discovers_rust_test_files_in_tests_dir(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "lib.rs").write_text("pub fn x() {}")
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "integration.rs").write_text("fn test_thing() {}")
        found = discover_tests(tmp_path, language="rust")
        paths = [t.path for t in found]
        assert "tests/integration.rs" in paths

    def test_discovers_rust_test_by_test_attribute(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "lib.rs").write_text(
            'pub fn add(a: i32, b: i32) -> i32 { a + b }\n\n'
            '#[cfg(test)]\nmod tests {\n    #[test]\n    fn test_add() {}\n}\n'
        )
        found = discover_tests(tmp_path, language="rust")
        paths = [t.path for t in found]
        assert "src/lib.rs" in paths

    def test_extracts_rust_test_names_from_test_file(self, tmp_path):
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "basic.rs").write_text(
            '#[test]\nfn test_one() { assert!(true); }\n'
            '#[test]\nfn test_two() { assert!(true); }\n'
        )
        found = discover_tests(tmp_path, language="rust")
        assert len(found) >= 1
        tf = found[0]
        assert "test_one" in tf.test_functions
        assert "test_two" in tf.test_functions


# ── render_stub_rust ──────────────────────────────────────────────────


class TestRenderStubRust:
    """Tests for render_stub_rust() in interface_stub."""

    def _make_contract(self, **kwargs) -> ComponentContract:
        defaults = dict(
            component_id="pricing",
            name="Pricing Engine",
            description="Calculate prices",
        )
        defaults.update(kwargs)
        return ComponentContract(**defaults)

    def test_basic_header(self):
        contract = self._make_contract()
        result = render_stub_rust(contract)
        assert "Pricing Engine" in result
        assert "pricing" in result

    def test_includes_serde_import(self):
        contract = self._make_contract()
        result = render_stub_rust(contract)
        assert "use serde::{Deserialize, Serialize};" in result
        assert "use thiserror::Error;" in result

    def test_renders_struct_type(self):
        contract = self._make_contract(
            types=[TypeSpec(
                name="PriceResult",
                kind="struct",
                description="Final price",
                fields=[
                    FieldSpec(name="amount", type_ref="float", required=True),
                    FieldSpec(name="currency", type_ref="str", required=False, default="USD"),
                ],
            )]
        )
        result = render_stub_rust(contract)
        assert "pub struct PriceResult" in result
        assert "#[derive(" in result
        assert "Serialize" in result
        assert "pub amount: f64," in result
        assert "pub currency: Option<String>," in result

    def test_renders_enum_type(self):
        contract = self._make_contract(
            types=[TypeSpec(
                name="Status",
                kind="enum",
                variants=["Active", "Inactive"],
            )]
        )
        result = render_stub_rust(contract)
        assert "pub enum Status" in result
        assert "Active," in result
        assert "Inactive," in result

    def test_renders_function_signature(self):
        contract = self._make_contract(
            functions=[FunctionContract(
                name="calculate",
                description="Calculate total price",
                inputs=[
                    FieldSpec(name="unit_id", type_ref="str"),
                    FieldSpec(name="quantity", type_ref="int"),
                ],
                output_type="float",
            )]
        )
        result = render_stub_rust(contract)
        assert "pub fn calculate(" in result
        assert "unit_id: &str," in result
        assert "quantity: i64," in result
        assert "-> f64" in result
        assert "todo!()" in result

    def test_async_function(self):
        contract = self._make_contract(
            functions=[FunctionContract(
                name="fetch",
                description="Fetch data",
                inputs=[],
                output_type="str",
                is_async=True,
            )]
        )
        result = render_stub_rust(contract)
        assert "pub async fn fetch()" in result

    def test_function_with_error_cases_uses_result(self):
        contract = self._make_contract(
            functions=[FunctionContract(
                name="parse",
                description="Parse input",
                inputs=[FieldSpec(name="input", type_ref="str")],
                output_type="str",
                error_cases=[
                    ErrorCase(
                        name="ParseError",
                        condition="invalid input",
                        error_type="ParseError",
                    ),
                ],
            )]
        )
        result = render_stub_rust(contract)
        assert "Result<String, ParseError>" in result

    def test_function_with_multiple_error_types_uses_box_dyn(self):
        contract = self._make_contract(
            functions=[FunctionContract(
                name="process",
                description="Process data",
                inputs=[],
                output_type="bool",
                error_cases=[
                    ErrorCase(name="IoError", condition="io failure", error_type="IoError"),
                    ErrorCase(name="ParseError", condition="parse failure", error_type="ParseError"),
                ],
            )]
        )
        result = render_stub_rust(contract)
        assert "Result<bool, Box<dyn std::error::Error>>" in result

    def test_renders_preconditions_in_doc(self):
        contract = self._make_contract(
            functions=[FunctionContract(
                name="divide",
                description="Divide a by b",
                inputs=[
                    FieldSpec(name="a", type_ref="float"),
                    FieldSpec(name="b", type_ref="float"),
                ],
                output_type="float",
                preconditions=["b != 0"],
            )]
        )
        result = render_stub_rust(contract)
        assert "Preconditions:" in result
        assert "b != 0" in result

    def test_renders_postconditions_in_doc(self):
        contract = self._make_contract(
            functions=[FunctionContract(
                name="abs_val",
                description="Absolute value",
                inputs=[FieldSpec(name="x", type_ref="float")],
                output_type="float",
                postconditions=["result >= 0"],
            )]
        )
        result = render_stub_rust(contract)
        assert "Postconditions:" in result
        assert "result >= 0" in result

    def test_renders_invariants(self):
        contract = self._make_contract(
            invariants=["prices must be non-negative", "currency is ISO 4217"],
        )
        result = render_stub_rust(contract)
        assert "Module invariants:" in result
        assert "prices must be non-negative" in result

    def test_renders_dependencies_in_header(self):
        contract = self._make_contract(
            dependencies=["inventory", "tax_calculator"],
        )
        result = render_stub_rust(contract)
        assert "inventory" in result
        assert "tax_calculator" in result

    def test_optional_input_parameter(self):
        contract = self._make_contract(
            functions=[FunctionContract(
                name="search",
                description="Search items",
                inputs=[
                    FieldSpec(name="query", type_ref="str"),
                    FieldSpec(name="limit", type_ref="int", required=False),
                ],
                output_type="list[str]",
            )]
        )
        result = render_stub_rust(contract)
        assert "limit: Option<i64>," in result

    def test_list_return_type(self):
        contract = self._make_contract(
            functions=[FunctionContract(
                name="get_names",
                description="Get names",
                inputs=[],
                output_type="list[str]",
            )]
        )
        result = render_stub_rust(contract)
        assert "Vec<String>" in result

    def test_renders_field_validators(self):
        contract = self._make_contract(
            types=[TypeSpec(
                name="Config",
                kind="struct",
                fields=[
                    FieldSpec(
                        name="port",
                        type_ref="int",
                        validators=[ValidatorSpec(kind="range", expression="1..65535")],
                    ),
                ],
            )]
        )
        result = render_stub_rust(contract)
        assert "range(1..65535)" in result


# ── _map_type_rust ────────────────────────────────────────────────────


class TestMapTypeRust:
    """Tests for the _map_type_rust type mapping helper."""

    def test_str_to_string(self):
        assert _map_type_rust("str") == "String"

    def test_int_to_i64(self):
        assert _map_type_rust("int") == "i64"

    def test_float_to_f64(self):
        assert _map_type_rust("float") == "f64"

    def test_bool_to_bool(self):
        assert _map_type_rust("bool") == "bool"

    def test_bytes_to_vec_u8(self):
        assert _map_type_rust("bytes") == "Vec<u8>"

    def test_none_to_unit(self):
        assert _map_type_rust("None") == "()"

    def test_any_to_serde_value(self):
        assert _map_type_rust("any") == "serde_json::Value"
        assert _map_type_rust("Any") == "serde_json::Value"

    def test_object_to_serde_value(self):
        assert _map_type_rust("object") == "serde_json::Value"

    def test_optional_str(self):
        assert _map_type_rust("Optional[str]") == "Option<String>"

    def test_optional_int(self):
        assert _map_type_rust("Optional[int]") == "Option<i64>"

    def test_list_str(self):
        assert _map_type_rust("list[str]") == "Vec<String>"

    def test_list_int(self):
        assert _map_type_rust("list[int]") == "Vec<i64>"

    def test_dict_str_int(self):
        result = _map_type_rust("dict[str, int]")
        assert "HashMap" in result
        assert "String" in result
        assert "i64" in result

    def test_dict_str_any(self):
        result = _map_type_rust("dict[str, any]")
        assert "HashMap" in result
        assert "serde_json::Value" in result

    def test_union_with_none_becomes_option(self):
        result = _map_type_rust("str | None")
        assert result == "Option<String>"

    def test_union_multiple_types_picks_first(self):
        result = _map_type_rust("str | int | float")
        assert result == "String"

    def test_user_defined_type_passthrough(self):
        assert _map_type_rust("PriceResult") == "PriceResult"

    def test_nested_optional_list(self):
        result = _map_type_rust("Optional[list[str]]")
        assert result == "Option<Vec<String>>"

    def test_dict_bare(self):
        result = _map_type_rust("dict")
        assert "HashMap" in result


# ── parse_cargo_test_output ───────────────────────────────────────────


class TestParseCargoTestOutput:
    """Tests for parse_cargo_test_output() in test_harness."""

    def test_parse_passing_tests(self):
        stdout = textwrap.dedent("""\
            running 3 tests
            test math::test_add ... ok
            test math::test_sub ... ok
            test math::test_mul ... ok

            test result: ok. 3 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out
        """)
        result = parse_cargo_test_output(stdout, "")
        assert result.passed == 3
        assert result.failed == 0
        assert result.total == 3

    def test_parse_failing_tests(self):
        stdout = textwrap.dedent("""\
            running 3 tests
            test math::test_add ... ok
            test math::test_sub ... FAILED
            test math::test_mul ... ok

            test result: FAILED. 2 passed; 1 failed; 0 ignored; 0 measured; 0 filtered out
        """)
        result = parse_cargo_test_output(stdout, "")
        assert result.passed == 2
        assert result.failed == 1
        assert result.total == 3
        assert len(result.failure_details) == 1
        assert result.failure_details[0].test_id == "math::test_sub"

    def test_parse_all_failing(self):
        stdout = textwrap.dedent("""\
            running 2 tests
            test core::test_a ... FAILED
            test core::test_b ... FAILED

            test result: FAILED. 0 passed; 2 failed; 0 ignored; 0 measured; 0 filtered out
        """)
        result = parse_cargo_test_output(stdout, "")
        assert result.passed == 0
        assert result.failed == 2
        assert result.total == 2

    def test_parse_summary_fallback(self):
        """When individual lines aren't present, falls back to summary."""
        stdout = "test result: ok. 10 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out\n"
        result = parse_cargo_test_output(stdout, "")
        assert result.passed == 10
        assert result.failed == 0
        assert result.total == 10

    def test_parse_compilation_error(self):
        stderr = textwrap.dedent("""\
            error[E0308]: mismatched types
              --> src/lib.rs:5:5
               |
            5  |     "hello"
               |     ^^^^^^^ expected `i32`, found `&str`

            error: could not compile `myproject` due to previous error
        """)
        result = parse_cargo_test_output("", stderr)
        assert result.errors == 1
        assert result.total == 1
        assert len(result.failure_details) == 1
        assert result.failure_details[0].test_id == "compilation"
        assert "compilation failed" in result.failure_details[0].error_message.lower()

    def test_parse_ignored_tests_not_counted(self):
        stdout = textwrap.dedent("""\
            running 3 tests
            test slow::test_perf ... ignored
            test math::test_add ... ok
            test math::test_sub ... ok

            test result: ok. 2 passed; 0 failed; 1 ignored; 0 measured; 0 filtered out
        """)
        result = parse_cargo_test_output(stdout, "")
        assert result.passed == 2
        assert result.failed == 0
        assert result.total == 2

    def test_parse_empty_output(self):
        result = parse_cargo_test_output("", "")
        assert result.total == 0
        assert result.passed == 0
        assert result.failed == 0
        assert result.errors == 0

    def test_parse_mixed_stderr_stdout(self):
        """Cargo sends compile output to stderr and test results to stdout."""
        stdout = "test basic::test_one ... ok\n"
        stderr = "   Compiling myproject v0.1.0\n    Finished test [unoptimized + debuginfo] target\n"
        result = parse_cargo_test_output(stdout, stderr)
        assert result.passed == 1
        assert result.total == 1

    def test_parse_multiple_test_binaries(self):
        stdout = textwrap.dedent("""\
            running 2 tests
            test alpha::test_a ... ok
            test alpha::test_b ... ok

            test result: ok. 2 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out

            running 1 test
            test beta::test_c ... FAILED

            test result: FAILED. 0 passed; 1 failed; 0 ignored; 0 measured; 0 filtered out
        """)
        result = parse_cargo_test_output(stdout, "")
        assert result.passed == 2
        assert result.failed == 1
        assert result.total == 3


# ── _generate_rust_smoke_tests ────────────────────────────────────────


class TestGenerateRustSmokeTests:
    """Tests for _generate_rust_smoke_tests() in adopt."""

    def _make_analysis(self, source_files=None) -> CodebaseAnalysis:
        return CodebaseAnalysis(
            root_path="/tmp/myproject",
            language="rust",
            source_files=source_files or [],
            test_files=[],
            coverage=CoverageMap(entries=[]),
            security=SecurityAuditReport(findings=[]),
        )

    def test_generates_cfg_test_module(self):
        analysis = self._make_analysis(source_files=[
            SourceFile(path="src/lib.rs", functions=[
                ExtractedFunction(name="greet", decorators=["pub"]),
            ]),
        ])
        suites = _generate_rust_smoke_tests(analysis)
        assert len(suites) >= 1
        # Get the generated code
        code = list(suites.values())[0]
        assert "#[cfg(test)]" in code

    def test_generates_test_attribute(self):
        analysis = self._make_analysis(source_files=[
            SourceFile(path="src/lib.rs", functions=[
                ExtractedFunction(name="greet", decorators=["pub"]),
            ]),
        ])
        suites = _generate_rust_smoke_tests(analysis)
        code = list(suites.values())[0]
        assert "#[test]" in code

    def test_generates_module_compilation_check(self):
        analysis = self._make_analysis(source_files=[
            SourceFile(path="src/utils.rs", functions=[
                ExtractedFunction(name="help", decorators=["pub"]),
            ]),
        ])
        suites = _generate_rust_smoke_tests(analysis)
        code = list(suites.values())[0]
        assert "compiles" in code

    def test_generates_function_existence_check(self):
        analysis = self._make_analysis(source_files=[
            SourceFile(path="src/math.rs", functions=[
                ExtractedFunction(name="add", decorators=["pub"]),
                ExtractedFunction(name="multiply", decorators=["pub"]),
            ]),
        ])
        suites = _generate_rust_smoke_tests(analysis)
        code = list(suites.values())[0]
        assert "test_add_exists" in code
        assert "test_multiply_exists" in code

    def test_uses_crate_path_for_imports(self):
        analysis = self._make_analysis(source_files=[
            SourceFile(path="src/math.rs", functions=[
                ExtractedFunction(name="add", decorators=["pub"]),
            ]),
        ])
        suites = _generate_rust_smoke_tests(analysis)
        code = list(suites.values())[0]
        assert "crate::math" in code

    def test_lib_rs_uses_crate_root(self):
        analysis = self._make_analysis(source_files=[
            SourceFile(path="src/lib.rs", functions=[
                ExtractedFunction(name="init"),
            ]),
        ])
        suites = _generate_rust_smoke_tests(analysis)
        code = list(suites.values())[0]
        # lib.rs should map to "crate" module path
        assert "crate" in code

    def test_skips_empty_source_files(self):
        analysis = self._make_analysis(source_files=[
            SourceFile(path="src/empty.rs", functions=[]),
        ])
        suites = _generate_rust_smoke_tests(analysis)
        assert len(suites) == 0

    def test_smoke_test_file_extension(self):
        analysis = self._make_analysis(source_files=[
            SourceFile(path="src/lib.rs", functions=[
                ExtractedFunction(name="foo"),
            ]),
        ])
        suites = _generate_rust_smoke_tests(analysis)
        for filename in suites:
            assert filename.endswith(".rs")

    def test_generate_smoke_tests_dispatches_to_rust(self):
        """generate_smoke_tests(language='rust') delegates to _generate_rust_smoke_tests."""
        analysis = self._make_analysis(source_files=[
            SourceFile(path="src/lib.rs", functions=[
                ExtractedFunction(name="bar"),
            ]),
        ])
        suites = generate_smoke_tests(analysis, language="rust")
        code = list(suites.values())[0]
        assert "#[cfg(test)]" in code

    def test_auto_generated_comment(self):
        analysis = self._make_analysis(source_files=[
            SourceFile(path="src/lib.rs", functions=[
                ExtractedFunction(name="foo"),
            ]),
        ])
        suites = _generate_rust_smoke_tests(analysis)
        code = list(suites.values())[0]
        assert "pact adopt" in code.lower() or "auto-generated" in code.lower()


# ── generate_rust_workflow ────────────────────────────────────────────


class TestGenerateRustWorkflow:
    """Tests for generate_rust_workflow() in ci."""

    def test_contains_cargo_test(self, tmp_path):
        workflow = generate_rust_workflow(tmp_path, ["tests/"])
        # Flatten all 'run' values from steps
        runs = _collect_runs(workflow)
        assert any("cargo test" in r for r in runs)

    def test_contains_clippy(self, tmp_path):
        workflow = generate_rust_workflow(tmp_path, ["tests/"])
        runs = _collect_runs(workflow)
        assert any("clippy" in r for r in runs)

    def test_contains_fmt(self, tmp_path):
        workflow = generate_rust_workflow(tmp_path, ["tests/"])
        runs = _collect_runs(workflow)
        assert any("cargo fmt" in r for r in runs)

    def test_uses_rust_toolchain_action(self, tmp_path):
        workflow = generate_rust_workflow(tmp_path, ["tests/"])
        steps = workflow["jobs"]["verify-contracts"]["steps"]
        uses = [s.get("uses", "") for s in steps]
        assert any("rust-toolchain" in u for u in uses)

    def test_requests_clippy_and_rustfmt_components(self, tmp_path):
        workflow = generate_rust_workflow(tmp_path, ["tests/"])
        steps = workflow["jobs"]["verify-contracts"]["steps"]
        for step in steps:
            if "rust-toolchain" in step.get("uses", ""):
                components = step.get("with", {}).get("components", "")
                assert "clippy" in components
                assert "rustfmt" in components

    def test_triggers_on_pull_request(self, tmp_path):
        workflow = generate_rust_workflow(tmp_path, ["tests/"])
        assert "pull_request" in workflow["on"]
        branches = workflow["on"]["pull_request"]["branches"]
        assert "main" in branches

    def test_runs_on_ubuntu(self, tmp_path):
        workflow = generate_rust_workflow(tmp_path, ["tests/"])
        assert workflow["jobs"]["verify-contracts"]["runs-on"] == "ubuntu-latest"

    def test_has_checkout_step(self, tmp_path):
        workflow = generate_rust_workflow(tmp_path, ["tests/"])
        steps = workflow["jobs"]["verify-contracts"]["steps"]
        uses = [s.get("uses", "") for s in steps]
        assert any("checkout" in u for u in uses)

    def test_workflow_name(self, tmp_path):
        workflow = generate_rust_workflow(tmp_path, ["tests/"])
        assert "name" in workflow
        assert isinstance(workflow["name"], str)


# ── Helpers ───────────────────────────────────────────────────────────


def _collect_runs(workflow: dict) -> list[str]:
    """Collect all 'run' field values from workflow steps."""
    runs: list[str] = []
    for job in workflow.get("jobs", {}).values():
        for step in job.get("steps", []):
            if "run" in step:
                runs.append(step["run"])
    return runs
