import 'dart:async';

abstract class RecitationRepository {
  // Returns a stream of data from the WebSocket
  Future<Stream<dynamic>> startStreaming(int surahId, int ayahId);
  Future<void> stopStreaming();
}