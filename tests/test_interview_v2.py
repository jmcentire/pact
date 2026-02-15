"""Tests for structured interview questions and answer audit trail."""
from pact.schemas import (
    QuestionType, InterviewQuestion, validate_answer,
    AnswerSource, AuditedAnswer, PerformanceBudget,
)


class TestQuestionTypes:
    def test_boolean_accepts_yes(self):
        q = InterviewQuestion(id="q1", text="Continue?", question_type=QuestionType.BOOLEAN)
        assert validate_answer(q, "yes") is None

    def test_boolean_accepts_no(self):
        q = InterviewQuestion(id="q1", text="Continue?", question_type=QuestionType.BOOLEAN)
        assert validate_answer(q, "no") is None

    def test_boolean_accepts_true(self):
        q = InterviewQuestion(id="q1", text="Continue?", question_type=QuestionType.BOOLEAN)
        assert validate_answer(q, "true") is None

    def test_boolean_accepts_false(self):
        q = InterviewQuestion(id="q1", text="Continue?", question_type=QuestionType.BOOLEAN)
        assert validate_answer(q, "false") is None

    def test_boolean_rejects_maybe(self):
        q = InterviewQuestion(id="q1", text="Continue?", question_type=QuestionType.BOOLEAN)
        result = validate_answer(q, "maybe")
        assert result is not None
        assert "yes/no" in result.lower() or "boolean" in result.lower()

    def test_enum_accepts_valid_option(self):
        q = InterviewQuestion(
            id="q2", text="Language?", question_type=QuestionType.ENUM,
            options=["Python", "TypeScript", "Rust"],
        )
        assert validate_answer(q, "Python") is None

    def test_enum_case_insensitive(self):
        q = InterviewQuestion(
            id="q2", text="Language?", question_type=QuestionType.ENUM,
            options=["Python", "TypeScript"],
        )
        assert validate_answer(q, "python") is None

    def test_enum_rejects_invalid(self):
        q = InterviewQuestion(
            id="q2", text="Language?", question_type=QuestionType.ENUM,
            options=["Python", "TypeScript"],
        )
        result = validate_answer(q, "Java")
        assert result is not None
        assert "Java" in result

    def test_numeric_in_range(self):
        q = InterviewQuestion(
            id="q3", text="Budget?", question_type=QuestionType.NUMERIC,
            range_min=0, range_max=100,
        )
        assert validate_answer(q, "42") is None

    def test_numeric_out_of_range_high(self):
        q = InterviewQuestion(
            id="q3", text="Budget?", question_type=QuestionType.NUMERIC,
            range_min=0, range_max=100,
        )
        result = validate_answer(q, "200")
        assert result is not None
        assert "above" in result.lower() or "maximum" in result.lower()

    def test_numeric_out_of_range_low(self):
        q = InterviewQuestion(
            id="q3", text="Budget?", question_type=QuestionType.NUMERIC,
            range_min=10, range_max=100,
        )
        result = validate_answer(q, "5")
        assert result is not None
        assert "below" in result.lower() or "minimum" in result.lower()

    def test_numeric_not_a_number(self):
        q = InterviewQuestion(
            id="q3", text="Budget?", question_type=QuestionType.NUMERIC,
        )
        result = validate_answer(q, "abc")
        assert result is not None
        assert "number" in result.lower()

    def test_numeric_no_range(self):
        q = InterviewQuestion(
            id="q3", text="Budget?", question_type=QuestionType.NUMERIC,
        )
        assert validate_answer(q, "999999") is None

    def test_freetext_accepts_nonempty(self):
        q = InterviewQuestion(id="q4", text="Describe the task")
        assert validate_answer(q, "Build a web app") is None

    def test_freetext_rejects_empty(self):
        q = InterviewQuestion(id="q4", text="Describe the task")
        result = validate_answer(q, "")
        assert result is not None
        assert "empty" in result.lower()

    def test_freetext_rejects_whitespace(self):
        q = InterviewQuestion(id="q4", text="Describe the task")
        result = validate_answer(q, "   ")
        assert result is not None

    def test_default_type_is_freetext(self):
        q = InterviewQuestion(id="q5", text="Any question")
        assert q.question_type == QuestionType.FREETEXT

    def test_conditional_fields(self):
        q = InterviewQuestion(
            id="q6", text="Which cloud?",
            question_type=QuestionType.ENUM,
            options=["AWS", "GCP"],
            depends_on="q5", depends_value="yes",
        )
        assert q.depends_on == "q5"
        assert q.depends_value == "yes"


class TestAuditedAnswer:
    def test_user_answer_full_confidence(self):
        a = AuditedAnswer(
            question_id="q1", answer="yes",
            source=AnswerSource.USER_INTERACTIVE,
            confidence=1.0, timestamp="2024-01-01T00:00:00",
        )
        assert a.confidence == 1.0
        assert a.source == AnswerSource.USER_INTERACTIVE

    def test_auto_answer_includes_assumption(self):
        a = AuditedAnswer(
            question_id="q1", answer="Python 3.12",
            source=AnswerSource.AUTO_ASSUMPTION,
            confidence=0.8,
            matched_assumption="We use Python 3.12 for all projects",
        )
        assert a.matched_assumption is not None
        assert "Python" in a.matched_assumption

    def test_low_confidence_value(self):
        a = AuditedAnswer(
            question_id="q1", answer="maybe",
            source=AnswerSource.CLI_APPROVE,
            confidence=0.3,
        )
        assert a.confidence < 0.5

    def test_answer_sources(self):
        for src in AnswerSource:
            a = AuditedAnswer(
                question_id="q1", answer="test",
                source=src, confidence=0.5,
            )
            assert a.source == src

    def test_slack_integration_source(self):
        a = AuditedAnswer(
            question_id="q1", answer="Approved via Slack",
            source=AnswerSource.INTEGRATION_SLACK,
            confidence=0.9,
        )
        assert a.source == AnswerSource.INTEGRATION_SLACK


class TestPerformanceBudget:
    def test_optional_by_default(self):
        from pact.schemas import FunctionContract
        fc = FunctionContract(name="test", description="test", inputs=[], output_type="str")
        assert fc.performance_budget is None

    def test_latency_budget(self):
        pb = PerformanceBudget(p95_latency_ms=100)
        assert pb.p95_latency_ms == 100
        assert pb.max_memory_mb is None

    def test_memory_budget(self):
        pb = PerformanceBudget(max_memory_mb=512)
        assert pb.max_memory_mb == 512

    def test_complexity_stored(self):
        pb = PerformanceBudget(complexity="O(n log n)")
        assert pb.complexity == "O(n log n)"

    def test_full_budget(self):
        pb = PerformanceBudget(p95_latency_ms=50, max_memory_mb=256, complexity="O(n)")
        assert pb.p95_latency_ms == 50
        assert pb.max_memory_mb == 256
        assert pb.complexity == "O(n)"

    def test_function_with_budget(self):
        from pact.schemas import FunctionContract
        pb = PerformanceBudget(p95_latency_ms=100)
        fc = FunctionContract(
            name="fast_lookup", description="Quick lookup",
            inputs=[], output_type="str",
            performance_budget=pb,
        )
        assert fc.performance_budget.p95_latency_ms == 100
