enum RecitationStatus { idle, recording, processing, success, retry, error }

class RecitationResult {
  final RecitationStatus status;
  final String? accuracy;
  final String? rawText;
  final String? expectedPhonemes;
  final String? predictedPhonemes;

  RecitationResult({
    required this.status,
    this.accuracy,
    this.rawText,
    this.expectedPhonemes,
    this.predictedPhonemes,
  });
}