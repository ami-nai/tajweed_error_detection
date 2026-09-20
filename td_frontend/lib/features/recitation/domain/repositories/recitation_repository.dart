import 'dart:async';

abstract class RecitationRepository {
  // Returns a stream of data from the WebSocket.
  // ayahId is null for surah mode, provided for single-ayah mode.
  Future<Stream<dynamic>> startStreaming(
    int surahId,
    int? ayahId, {
    String mode = 'single',
  });
  Future<void> nextAyah();
  Future<void> stopStreaming();
}