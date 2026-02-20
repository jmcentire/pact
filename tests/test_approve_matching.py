"""Tests for approve matching logic."""
import argparse
from io import StringIO
from unittest.mock import MagicMock, patch

from pact.cli import cmd_approve, cmd_status, match_answer_to_question, STOPWORDS
from pact.schemas import InterviewResult, RunState


class TestMatchAnswerToQuestion:
    def test_keyword_overlap_match(self):
        question = "What database should we use for persistence?"
        assumptions = [
            "PostgreSQL will be used for data persistence",
            "Redis will be used for caching",
        ]
        answer, confidence = match_answer_to_question(question, assumptions)
        assert "persistence" in answer.lower() or "PostgreSQL" in answer
        assert confidence > 0.0

    def test_index_fallback(self):
        question = "Completely unrelated question about nothing?"
        assumptions = ["First assumption", "Second assumption"]
        answer, confidence = match_answer_to_question(question, assumptions, question_index=0)
        assert answer == "First assumption"
        assert confidence == 0.5

    def test_no_match_returns_accepted(self):
        question = "Completely unrelated?"
        assumptions = []
        answer, confidence = match_answer_to_question(question, assumptions)
        assert answer == "Accepted as stated"
        assert confidence == 0.0

    def test_stopwords_excluded(self):
        """'is the for to' should not count as keyword matches."""
        question = "What is the best approach for this?"
        assumptions = ["The approach is to use microservices for the system"]
        # Only "approach" is a significant shared word, need >= 2
        answer, confidence = match_answer_to_question(question, assumptions)
        # Should either match on shared words or fall to index
        assert answer is not None

    def test_no_universal_fallback(self):
        """Different questions shouldn't all get the same assumption."""
        assumptions = ["Use PostgreSQL", "Use REST APIs"]
        questions = [
            "What storage technology?",
            "What API protocol?",
            "What deployment strategy?",
            "What monitoring approach?",
            "What testing framework?",
        ]
        answers = set()
        for i, q in enumerate(questions):
            answer, _ = match_answer_to_question(q, assumptions, question_index=i)
            answers.add(answer)
        # Should not all be the same answer
        assert len(answers) > 1

    def test_confidence_between_0_and_1(self):
        question = "What database technology should we use for data storage and persistence?"
        assumptions = ["PostgreSQL for database storage and persistence layer"]
        _, confidence = match_answer_to_question(question, assumptions)
        assert 0.0 <= confidence <= 1.0

    def test_index_pairing_second_question(self):
        question = "Unrelated question?"
        assumptions = ["First", "Second", "Third"]
        answer, confidence = match_answer_to_question(question, assumptions, question_index=1)
        assert answer == "Second"
        assert confidence == 0.5

    def test_keyword_match_beats_index(self):
        question = "How should we handle authentication and authorization?"
        assumptions = [
            "REST API design",
            "OAuth2 for authentication and JWT for authorization",
        ]
        answer, confidence = match_answer_to_question(question, assumptions, question_index=0)
        # Keyword match on "authentication"+"authorization" should beat index
        assert "auth" in answer.lower()
        assert confidence >= 0.5


class TestStopwords:
    def test_stopwords_are_common_words(self):
        for word in ["the", "is", "are", "for", "to", "in", "of", "and"]:
            assert word in STOPWORDS

    def test_significant_words_not_in_stopwords(self):
        for word in ["database", "authentication", "service", "component"]:
            assert word not in STOPWORDS


class TestCmdApproveSummary:
    """Tests that cmd_approve prints answer sources correctly."""

    def test_approve_prints_user_vs_auto(self, capsys):
        """Approve should print which answers are user vs auto-matched."""
        interview = InterviewResult(
            risks=["risk1"],
            ambiguities=[],
            questions=["What crypto library?", "Target language?"],
            assumptions=["TweetNaCl for cryptography library", "TypeScript with strict mode"],
            user_answers={"What crypto library?": "TweetNaCl.js"},
            approved=False,
        )

        project = MagicMock()
        project.load_interview.return_value = interview
        project.save_interview = MagicMock()

        args = argparse.Namespace(
            project_dir="/tmp/test",
            interactive=False,
        )

        with patch("pact.cli.ProjectManager", return_value=project):
            with patch("pact.daemon.send_signal", return_value=False):
                cmd_approve(args)

        captured = capsys.readouterr()
        assert "[user]" in captured.out
        assert "[auto" in captured.out
        assert "TweetNaCl.js" in captured.out

    def test_approve_preserves_existing_answers(self):
        """Existing user answers should not be overwritten by auto-matching."""
        interview = InterviewResult(
            risks=[],
            ambiguities=[],
            questions=["Q1?", "Q2?"],
            assumptions=["A1", "A2"],
            user_answers={"Q1?": "My custom answer"},
            approved=False,
        )

        project = MagicMock()
        project.load_interview.return_value = interview
        project.save_interview = MagicMock()

        args = argparse.Namespace(
            project_dir="/tmp/test",
            interactive=False,
        )

        with patch("pact.cli.ProjectManager", return_value=project):
            with patch("pact.daemon.send_signal", return_value=False):
                cmd_approve(args)

        # Check that Q1's answer was preserved
        saved_interview = project.save_interview.call_args[0][0]
        assert saved_interview.user_answers["Q1?"] == "My custom answer"


class TestCmdStatusInterview:
    """Tests that cmd_status shows interview answer summary."""

    def test_status_shows_interview_summary(self, capsys):
        """Status should display interview questions and answers."""
        interview = InterviewResult(
            risks=[],
            ambiguities=[],
            questions=["What crypto library?", "Target language?"],
            assumptions=[],
            user_answers={"What crypto library?": "TweetNaCl.js"},
            approved=False,
        )
        state = RunState(id="test123", project_dir="/tmp/test")

        project = MagicMock()
        project.has_state.return_value = True
        project.load_state.return_value = state
        project.load_interview.return_value = interview
        project.load_tree.return_value = None
        project.load_audit.return_value = None

        args = argparse.Namespace(
            project_dir="/tmp/test",
            component_id=None,
        )

        with patch("pact.cli.ProjectManager", return_value=project):
            with patch("pact.daemon.check_daemon_health", return_value={"alive": False, "fifo_exists": False}):
                cmd_status(args)

        captured = capsys.readouterr()
        assert "2 questions, 1 answered (1 pending)" in captured.out
        assert "[answered]" in captured.out
        assert "[pending]" in captured.out
        assert "TweetNaCl.js" in captured.out
        assert "not approved" in captured.out

    def test_status_no_interview(self, capsys):
        """Status should not crash when no interview exists."""
        state = RunState(id="test123", project_dir="/tmp/test")

        project = MagicMock()
        project.has_state.return_value = True
        project.load_state.return_value = state
        project.load_interview.return_value = None
        project.load_tree.return_value = None
        project.load_audit.return_value = None

        args = argparse.Namespace(
            project_dir="/tmp/test",
            component_id=None,
        )

        with patch("pact.cli.ProjectManager", return_value=project):
            with patch("pact.daemon.check_daemon_health", return_value={"alive": False, "fifo_exists": False}):
                cmd_status(args)

        captured = capsys.readouterr()
        assert "Interview" not in captured.out
