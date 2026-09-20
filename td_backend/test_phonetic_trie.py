"""Unit tests for the phonetic trie (PhoneticTrie.search_fuzzy)."""

from phonetic_trie import PhoneticTrie, build_global_ayah_trie_by_ayah


STREAMS = {
    (111, 1): "تَبَّتْ يَدَا أَبِي لَهَبٍ وَتَبَّ",
    (111, 2): "مَا أَغْنَىٰ عَنْهُ مَالُهُ وَمَا كَسَبَ",
    (113, 1): "قُلْ أَعُوذُ بِرَبِّ الْفَلَقِ",
    (113, 2): "مِن شَرِّ مَا خَلَقَ",
    (114, 1): "قُلْ أَعُوذُ بِرَبِّ النَّاسِ",
    (114, 4): "مِن شَرِّ الْوَسْوَاسِ الْخَنَّاسِ",
}


def make_trie():
    return build_global_ayah_trie_by_ayah(
        [(s, a, st) for (s, a), st in STREAMS.items()]
    )


def test_exact_prefix_resolves_to_ayah():
    trie = make_trie()
    res = trie.search_fuzzy(list("قُلْ أَعُوذُ بِرَبِّ النَّاسِ"), min_tokens=5)
    assert res is not None
    assert res[0] == 114 and res[1] == 1


def test_shared_prefix_is_ambiguous():
    trie = make_trie()
    # 113:1 and 114:1 share "قُلْ أَعُوذُ بِرَبِّ" — short query cannot tell them apart.
    res = trie.search_fuzzy(list("قُلْ أَعُوذُ بِرَبِّ"), min_tokens=5)
    assert res is None


def test_divergence_resolves_ambiguity():
    trie = make_trie()
    # After the shared prefix diverges (الْفَلَقِ vs النَّاسِ) the ayah is unique.
    res = trie.search_fuzzy(list("قُلْ أَعُوذُ بِرَبِّ الْفَلَقِ"), min_tokens=5)
    assert res is not None
    assert res[0] == 113 and res[1] == 1


def test_fuzzy_within_budget_matches():
    trie = make_trie()
    noisy = list("قُلْ أَعُوزُ بِرَبِّ الْفَلَقِ")  # one substitution
    res = trie.search_fuzzy(noisy, err_rate=0.35, min_tokens=5)
    assert res is not None
    assert res[0] == 113 and res[1] == 1


def test_short_query_rejected_below_min_tokens():
    trie = make_trie()
    res = trie.search_fuzzy(list("قُلْ"), min_tokens=5)
    assert res is None


def test_allowed_surahs_filter():
    trie = make_trie()
    # Closest ayah is 113:1, but restricted to surah 111 → no match (or 111 ayahs).
    res = trie.search_fuzzy(
        list("قُلْ أَعُوذُ بِرَبِّ الْفَلَقِ"), allowed_surahs={111}, min_tokens=5
    )
    assert res is None or res[0] == 111


def test_bad_stream_rejected():
    trie = make_trie()
    gibberish = "مِيم مِيم مِيم مِيم مِيم مِيم مِيم مِيم مِيم مِيم"
    res = trie.search_fuzzy(list(gibberish), err_rate=0.35, min_tokens=5)
    assert res is None


def test_insert_dedupe_shared_prefix_counts_both_ayahs():
    trie = make_trie()
    node = trie.root.children.get("ق")
    assert node is not None
    assert (113, 1) in node.ayahs
    assert (114, 1) in node.ayahs