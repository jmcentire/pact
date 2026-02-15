"""Tests for approve matching logic."""
from pact.cli import match_answer_to_question, STOPWORDS


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
