enum RecitationStatus { idle, recording, processing, success, retry, error }

enum RecitationMode { singleAyah, surah, openMic }

enum LetterStatus { ok, miss, neutral }

// Sentinel used by RecitationResult.copyWith to allow openMicAyah to be reset
// to null (since the regular `Object?` + `??` pattern can't distinguish a
// caller passing `null` from a caller omitting the argument).
const _omit = Object();

class LetterHit {
  final String ch;
  final LetterStatus status;

  LetterHit({required this.ch, required this.status});

  factory LetterHit.fromJson(Map<String, dynamic> json) {
    final status = switch (json['status']?.toString()) {
      'ok' => LetterStatus.ok,
      'miss' => LetterStatus.miss,
      _ => LetterStatus.neutral,
    };
    return LetterHit(ch: json['ch']?.toString() ?? '', status: status);
  }
}

class DiffHit {
  final String? e;
  final String? h;
  final String status; // 'M' match, 'S' substitution, 'D' deletion, 'I' insertion

  DiffHit({this.e, this.h, required this.status});

  factory DiffHit.fromJson(Map<String, dynamic> json) {
    return DiffHit(
      e: json['e']?.toString(),
      h: json['h']?.toString(),
      status: json['status']?.toString() ?? 'S',
    );
  }
}

class RecitationResult {
  // Single-ayah mode fields
  final List<WordTrackResult> words;
  final RecitationStatus status;
  final int selectedSurah;
  final int selectedAyah;
  final String realText;
  final String expected;
  final String predicted;
  final double accuracy;
  final double per;

  // Mode
  final RecitationMode mode;

  // Open-mic: the ayah id the currently displayed words belong to (null until
  // detection, and bumped when the server re-detects a later ayah).
  final int? openMicAyah;

  // Open-mic: raw decoded phoneme stream as it is produced by the server.
  final String livePhonemes;

  // Live "spotlight": index of the word currently expected next, within
  // whichever ayah is on screen (selectedAyah / currentAyah / openMicAyah).
  // Null when nothing is active (idle, or just finalized).
  final int? activeWordIndex;

  // Surah (sequential) mode fields
  final Map<int, List<WordTrackResult>> surahWords;
  final Map<int, double> ayahAccuracies;
  final double surahAverage;
  final int currentAyah;
  final int nextAyah;
  final Map<int, double> ayahPER;
  final double surahPER;

  // For the colored transcription diff (expected vs predicted)
  final List<DiffHit> diff;

  // Plain-text mistake feedback
  final List<String> mistakes;
  final Map<int, List<String>> ayahMistakes;

  // Per-ayah transcriptions (surah mode): diff + raw expected/predicted strings
  final Map<int, List<DiffHit>> ayahDiffs;
  final Map<int, String> ayahExpected;
  final Map<int, String> ayahPredicted;

  // Thesis round-trip measurement (edge device, same-device clock):
  // per-beat mic-send -> interim-recv latency in ms. Empty/zero until the
  // first interim of a recording arrives; reset on every new recording.
  final List<int> rttSamples;
  final int rttLastMs;
  final int rttMedianMs;
  final int rttMinMs;
  final int rttMaxMs;
  final int rttBeats;

  RecitationResult({
    required this.words,
    this.status = RecitationStatus.idle,
    this.selectedSurah = 1,
    this.selectedAyah = 1,
    this.realText = "",
    this.expected = "",
    this.predicted = "",
    this.accuracy = 0,
    this.per = 0,
    this.mode = RecitationMode.singleAyah,
    this.openMicAyah,
    this.livePhonemes = '',
    this.activeWordIndex,
    Map<int, List<WordTrackResult>>? surahWords,
    Map<int, double>? ayahAccuracies,
    this.surahAverage = 0,
    this.currentAyah = 0,
    this.nextAyah = 0,
    Map<int, double>? ayahPER,
    this.surahPER = 0,
    List<DiffHit>? diff,
    List<String>? mistakes,
    Map<int, List<String>>? ayahMistakes,
    Map<int, List<DiffHit>>? ayahDiffs,
    Map<int, String>? ayahExpected,
    Map<int, String>? ayahPredicted,
    List<int>? rttSamples,
    this.rttLastMs = 0,
    this.rttMedianMs = 0,
    this.rttMinMs = 0,
    this.rttMaxMs = 0,
    this.rttBeats = 0,
  })  : surahWords = surahWords ?? {},
        ayahAccuracies = ayahAccuracies ?? {},
        ayahPER = ayahPER ?? {},
        diff = diff ?? [],
        mistakes = mistakes ?? [],
        ayahMistakes = ayahMistakes ?? {},
        ayahDiffs = ayahDiffs ?? {},
        ayahExpected = ayahExpected ?? {},
        ayahPredicted = ayahPredicted ?? {},
        rttSamples = rttSamples ?? [];

  factory RecitationResult.initial() {
    return RecitationResult(
      words: [],
      status: RecitationStatus.idle,
      selectedSurah: 1,
      selectedAyah: 1,
    );
  }

  // Modified to take surahId and ayahId context so we preserve our current viewport coordinates
  factory RecitationResult.fromJson(Map<String, dynamic> json, int surahId, int ayahId) {
    return RecitationResult(
      words: (json['words'] as List)
          .map((i) => WordTrackResult.fromJson(i))
          .toList(),
      status: RecitationStatus.recording,
      selectedSurah: surahId,
      selectedAyah: ayahId,
      realText: json['real_text']?.toString() ?? '',
      expected: json['expected']?.toString() ?? '',
      predicted: json['predicted']?.toString() ?? '',
      accuracy: (json['accuracy'] as num?)?.toDouble() ?? 0,
      per: (json['per'] as num?)?.toDouble() ?? 0,
    );
  }

  RecitationResult copyWith({
    List<WordTrackResult>? words,
    RecitationStatus? status,
    int? selectedSurah,
    int? selectedAyah,
    String? realText,
    String? expected,
    String? predicted,
    double? accuracy,
    double? per,
    RecitationMode? mode,
    Object? openMicAyah = _omit,
    String? livePhonemes,
    Object? activeWordIndex = _omit,
    Map<int, List<WordTrackResult>>? surahWords,
    Map<int, double>? ayahAccuracies,
    double? surahAverage,
    int? currentAyah,
    int? nextAyah,
    Map<int, double>? ayahPER,
    double? surahPER,
    List<DiffHit>? diff,
    List<String>? mistakes,
    Map<int, List<String>>? ayahMistakes,
    Map<int, List<DiffHit>>? ayahDiffs,
    Map<int, String>? ayahExpected,
    Map<int, String>? ayahPredicted,
    List<int>? rttSamples,
    int? rttLastMs,
    int? rttMedianMs,
    int? rttMinMs,
    int? rttMaxMs,
    int? rttBeats,
  }) {
    return RecitationResult(
      words: words ?? this.words,
      status: status ?? this.status,
      selectedSurah: selectedSurah ?? this.selectedSurah,
      selectedAyah: selectedAyah ?? this.selectedAyah,
      realText: realText ?? this.realText,
      expected: expected ?? this.expected,
      predicted: predicted ?? this.predicted,
      accuracy: accuracy ?? this.accuracy,
      per: per ?? this.per,
      mode: mode ?? this.mode,
      openMicAyah: identical(openMicAyah, _omit)
          ? this.openMicAyah
          : openMicAyah as int?,
      livePhonemes: livePhonemes ?? this.livePhonemes,
      activeWordIndex: identical(activeWordIndex, _omit)
          ? this.activeWordIndex
          : activeWordIndex as int?,
      surahWords: surahWords ?? this.surahWords,
      ayahAccuracies: ayahAccuracies ?? this.ayahAccuracies,
      surahAverage: surahAverage ?? this.surahAverage,
      currentAyah: currentAyah ?? this.currentAyah,
      nextAyah: nextAyah ?? this.nextAyah,
      ayahPER: ayahPER ?? this.ayahPER,
      surahPER: surahPER ?? this.surahPER,
      diff: diff ?? this.diff,
      mistakes: mistakes ?? this.mistakes,
      ayahMistakes: ayahMistakes ?? this.ayahMistakes,
      ayahDiffs: ayahDiffs ?? this.ayahDiffs,
      ayahExpected: ayahExpected ?? this.ayahExpected,
      ayahPredicted: ayahPredicted ?? this.ayahPredicted,
      rttSamples: rttSamples ?? this.rttSamples,
      rttLastMs: rttLastMs ?? this.rttLastMs,
      rttMedianMs: rttMedianMs ?? this.rttMedianMs,
      rttMinMs: rttMinMs ?? this.rttMinMs,
      rttMaxMs: rttMaxMs ?? this.rttMaxMs,
      rttBeats: rttBeats ?? this.rttBeats,
    );
  }
}

class WordTrackResult {
  final String text;
  final bool isRead;
  final List<LetterHit> letters;

  WordTrackResult({required this.text, required this.isRead, List<LetterHit>? letters})
      : letters = letters ?? [];

  factory WordTrackResult.fromJson(Map<String, dynamic> json) {
    final letters = (json['letters'] as List?)?.map((e) {
      if (e is Map) {
        return LetterHit.fromJson(e.cast<String, dynamic>());
      }
      return LetterHit(ch: e.toString(), status: LetterStatus.neutral);
    }).toList();
    return WordTrackResult(
      text: json['text'],
      isRead: json['is_read'] ?? false,
      letters: letters,
    );
  }

  WordTrackResult copyWith({bool? isRead, List<LetterHit>? letters}) {
    return WordTrackResult(
      text: text,
      isRead: isRead ?? this.isRead,
      letters: letters ?? this.letters,
    );
  }
}
