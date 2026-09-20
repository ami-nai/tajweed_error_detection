"""Phonetic trie for fast fuzzy matching of phoneme streams.

Two consumers live on top of this structure:

- A global ayah trie (insert each ayah's full phoneme stream) used for Open-Mic
  resolution: given a partial predicted phoneme stream, decide *which* ayah the
  user is reciting.
- Per-ayah guided word tries used by the live highlighting pointer.

The matcher is a bounded Levenshtein automaton over the trie (same idea as the
C++ plan, kept in pure Python because the index is only ~104 ayahs / a few
thousand tokens, so lookups are effectively instant).
"""


class TrieNode:
    __slots__ = ("children", "end", "surah_id", "ayah_id", "word_idx", "ayahs")

    def __init__(self):
        self.children = {}
        self.end = False
        self.surah_id = None
        self.ayah_id = None
        self.word_idx = None
        # Every ayah whose path passes through this node. Kept as a set so two
        # ayahs sharing a prefix are both marked on the shared nodes, which lets
        # fuzzy prefix-matching attribute a matched node to all ayahs that use it.
        self.ayahs = set()


def _next_row(prev, query, ch):
    """Advance a DP row (edit distance between query prefixes and the trie path
    so far) one trie character deeper."""
    n = len(query)
    row = [0] * (n + 1)
    row[0] = prev[0] + 1
    for i in range(1, n + 1):
        cost = 0 if query[i - 1] == ch else 1
        row[i] = min(
            prev[i - 1] + cost,
            prev[i] + 1,
            row[i - 1] + 1,
        )
    return row


class PhoneticTrie:
    def __init__(self):
        self.root = TrieNode()
        self.size = 0

    def insert(self, tokens, surah_id=None, ayah_id=None, word_idx=None):
        node = self.root
        for tok in tokens:
            node = node.children.setdefault(tok, TrieNode())
            if surah_id is not None and ayah_id is not None:
                node.ayahs.add((surah_id, ayah_id))
        if node.end:
            return
        node.end = True
        node.surah_id = surah_id
        node.ayah_id = ayah_id
        node.word_idx = word_idx
        self.size += 1

    def search_fuzzy(
        self,
        query,
        allowed_surahs=None,
        err_rate=0.35,
        min_tokens=5,
        lo_ratio=0.7,
        hi_ratio=1.4,
        margin_rel=0.15,
        margin_abs=0.04,
    ):
        """Find the ayah whose phoneme stream best matches a (possibly partial)
        predicted phoneme stream.

        Returns a tuple (surah_id, ayah_id, cost, score, depth) or None.

        score = cost / max(len(query), depth): normalizes so short queries can
        be compared across ayahs of different lengths. The search only considers
        trie depths within [lo_ratio, hi_ratio] x len(query) so an oversized
        ayah can't win just by swallowing a short query through insertions.

        Pruning: child rows are pointwise >= their parent rows, so a branch whose
        minimum already exceeds the budget can never produce an accepted match.
        """
        n = len(query)
        if n < min_tokens:
            return None
        budget = max(2, int(n * err_rate))
        lo_depth = max(min_tokens, int(n * lo_ratio))
        hi_depth = int(n * hi_ratio)

        # (surah, ayah) -> (cost, score, depth)
        best = {}

        def visit(node, row, depth):
            if lo_depth <= depth <= hi_depth and row[n] <= budget:
                cost = row[n]
                score = cost / max(n, depth)
                for sa in node.ayahs:
                    if allowed_surahs is not None and sa[0] not in allowed_surahs:
                        continue
                    cur = best.get(sa)
                    if cur is None or score < cur[1] or (score == cur[1] and cost < cur[0]):
                        best[sa] = (cost, score, depth)
            if depth >= hi_depth:
                return
            if min(row) > budget:
                return
            for ch, child in node.children.items():
                next_row = _next_row(row, query, ch)
                if min(next_row) > budget:
                    continue
                visit(child, next_row, depth + 1)

        visit(self.root, list(range(n + 1)), 0)

        if not best:
            return None

        ranked = sorted(best.items(), key=lambda kv: (kv[1][1], kv[1][0]))
        (s, a), (cost, score, depth) = ranked[0]
        if score > err_rate:
            return None
        if len(ranked) > 1:
            (_, _), (_, second_score, _) = ranked[1]
            if (second_score - score) < (score * margin_rel + margin_abs):
                # Two ayahs are indistinguishable with this much audio yet.
                return None
        return (s, a, cost, round(score, 4), depth)


def build_global_ayah_trie_by_ayah(ayah_streams):
    """ayah_streams: iterable of (surah_id, ayah_id, phoneme_stream_str)."""
    trie = PhoneticTrie()
    for surah, ayah, stream in ayah_streams:
        trie.insert(list(stream), surah_id=surah, ayah_id=ayah)
    return trie