from backend.main import SentenceAccumulator


def test_sentence_accumulator_flushes_complete_sentences_incrementally():
    acc = SentenceAccumulator()

    assert acc.push("Hello there.") == ["Hello there."]
    assert acc.push(" How are") == []
    assert acc.push(" you? Fine") == ["How are you?"]
    assert acc.drain() == ["Fine"]
