enum RecitationStatus { idle, recording, processing, success, retry, error }

class RecitationResult {
  final List<WordTrackResult> words;
  final RecitationStatus status;
  final int selectedSurah;
  final int selectedAyah;
  final String realText;
  final String expected;
  final String predicted;
  final double accuracy;

  RecitationResult({
    required this.words,
    this.status = RecitationStatus.idle,
    this.selectedSurah = 1,
    this.selectedAyah = 1,
    this.realText = "",
    this.expected = "",
    this.predicted = "",
    this.accuracy = 0,
  });

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
}