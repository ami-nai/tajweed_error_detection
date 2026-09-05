enum RecitationStatus { idle, recording, processing, success, retry, error }

enum RecitationMode { singleAyah, surah }

enum LetterStatus { ok, miss, neutral }

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
  final double wer;

  // Mode
  final RecitationMode mode;

  // Surah (sequential) mode fields
  final Map<int, List<WordTrackResult>> surahWords;
  final Map<int, double> ayahAccuracies;
  final double surahAverage;
  final int currentAyah;
  final int nextAyah;
  final Map<int, double> ayahWER;
  final double surahWER;

  // For the colored transcription diff (expected vs predicted)
  final List<DiffHit> diff;

  RecitationResult({
    required this.words,
    this.status = RecitationStatus.idle,
    this.selectedSurah = 1,
    this.selectedAyah = 1,
    this.realText = "",
    this.expected = "",
    this.predicted = "",
    this.accuracy = 0,
    this.wer = 0,
    this.mode = RecitationMode.singleAyah,
    Map<int, List<WordTrackResult>>? surahWords,
    Map<int, double>? ayahAccuracies,
    this.surahAverage = 0,
    this.currentAyah = 0,
    this.nextAyah = 0,
    Map<int, double>? ayahWER,
    this.surahWER = 0,
    List<DiffHit>? diff,
  })  : surahWords = surahWords ?? {},
        ayahAccuracies = ayahAccuracies ?? {},
        ayahWER = ayahWER ?? {},
        diff = diff ?? [];

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
      wer: (json['wer'] as num?)?.toDouble() ?? 0,
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
    double? wer,
    RecitationMode? mode,
    Map<int, List<WordTrackResult>>? surahWords,
    Map<int, double>? ayahAccuracies,
    double? surahAverage,
    int? currentAyah,
    int? nextAyah,
    Map<int, double>? ayahWER,
    double? surahWER,
    List<DiffHit>? diff,
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
      wer: wer ?? this.wer,
      mode: mode ?? this.mode,
      surahWords: surahWords ?? this.surahWords,
      ayahAccuracies: ayahAccuracies ?? this.ayahAccuracies,
      surahAverage: surahAverage ?? this.surahAverage,
      currentAyah: currentAyah ?? this.currentAyah,
      nextAyah: nextAyah ?? this.nextAyah,
      ayahWER: ayahWER ?? this.ayahWER,
      surahWER: surahWER ?? this.surahWER,
      diff: diff ?? this.diff,
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
