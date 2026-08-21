import pytest

from services.corpus.aligner import align_sequences, score_pair
from services.corpus.dedup import PairDeduplicator, pair_hash

pytestmark = pytest.mark.unit

ZH = ["中国坚持绿色发展。", "2023年清洁能源消费比重达到26.4%。", "十年来成就显著。"]
EN = ["China pursues green development.",
      "In 2023, the share of clean energy consumption reached 26.4%.",
      "The past decade saw great gains."]


class TestScorePair:
    def test_parallel_scores_high(self):
        assert score_pair(ZH[1], EN[1]) > 0.6

    def test_unrelated_scores_lower(self):
        assert score_pair(ZH[1], EN[2]) < score_pair(ZH[1], EN[1])

    def test_number_anchor(self):
        with_num = score_pair(ZH[1], EN[1])
        without_num = score_pair(ZH[1], "In 2023, the share of clean energy consumption rose a lot.")
        assert with_num > without_num


class TestAlignSequences:
    def test_one_to_one(self):
        aligned = align_sequences(ZH, EN, allow_merges=False)
        assert len(aligned) == 3
        for a in aligned:
            assert ZH[a.zh_idx[0]] and EN[a.en_idx[0]]

    def test_sentence_split_then_merge(self):
        zh_sents = ["推动高质量发展。", "加快构建新发展格局。"]
        en_sents = ["We will promote high-quality development and accelerate a new development pattern."]
        aligned = align_sequences(zh_sents, en_sents, allow_merges=True)
        assert aligned[0].zh_idx == [0, 1]

    def test_noise_sentence_skipped(self):
        zh = ZH + ["这是一句完全没有对应翻译的话而且非常独特。"]
        aligned = align_sequences(zh, EN, allow_merges=False, min_score=0.2)
        assert len(aligned) == 3


class TestDedup:
    def test_keeps_higher_score(self):
        d = PairDeduplicator()
        assert d.add("深化改革", "deepen reform", 0.5)
        assert not d.add("深化改革", "deepen reform", 0.4)
        assert d.add("深化改革", "deepen reform", 0.9)
        assert len(d.kept) == 1
        assert d.kept[0][2] == 0.9
        assert d.dropped == 2

    def test_normalization(self):
        assert pair_hash("深化  改革", "Deepen   Reform") == pair_hash("深化改革", "deepen reform")
