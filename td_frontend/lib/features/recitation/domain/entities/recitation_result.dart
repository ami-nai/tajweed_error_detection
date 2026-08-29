enum RecitationStatus { idle, recording, processing, success, retry, error }

enum RecitationMode { singleAyah, surah }

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

  // Mode
  final RecitationMode mode;

  // Surah (sequential) mode fields
  final Map<int, List<WordTrackResult>> surahWords;
  final Map<int, double> ayahAccuracies;
  final double surahAverage;
  final int currentAyah;
  final int nextAyah;

  RecitationResult({
    required this.words,
    this.status = RecitationStatus.idle,
    this.selectedSurah = 1,
    this.selectedAyah = 1,
    this.realText = "",
    this.expected = "",
    this.predicted = "",
    this.accuracy = 0,
    this.mode = RecitationMode.singleAyah,
    Map<int, List<WordTrackResult>>? surahWords,
    Map<int, double>? ayahAccuracies,
    this.surahAverage = 0,
    this.currentAyah = 0,
    this.nextAyah = 0,
  })  : surahWords = surahWords ?? {},
        ayahAccuracies = ayahAccuracies ?? {};

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
    RecitationMode? mode,
    Map<int, List<WordTrackResult>>? surahWords,
    Map<int, double>? ayahAccuracies,
    double? surahAverage,
    int? currentAyah,
    int? nextAyah,
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
      mode: mode ?? this.mode,
      surahWords: surahWords ?? this.surahWords,
      ayahAccuracies: ayahAccuracies ?? this.ayahAccuracies,
      surahAverage: surahAverage ?? this.surahAverage,
      currentAyah: currentAyah ?? this.currentAyah,
      nextAyah: nextAyah ?? this.nextAyah,
    );
  }
}

class WordTrackResult {
  final String text;
  final bool isRead;

  WordTrackResult({required this.text, required this.isRead});

  factory WordTrackResult.fromJson(Map<String, dynamic> json) {
    return WordTrackResult(
      text: json['text'],
      isRead: json['is_read'] ?? false,
    );
  }

  WordTrackResult copyWith({bool? isRead}) {
    return WordTrackResult(text: text, isRead: isRead ?? this.isRead);
  }
}
