import 'dart:async';

abstract class RecitationRepository {
  // Returns a stream of data from the WebSocket.
  // ayahId is null for surah (sequential) mode, provided for single-ayah mode.
  Future<Stream<dynamic>> startStreaming(int surahId, int? ayahId);
  Future<void> stopStreaming();
}