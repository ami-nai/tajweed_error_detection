import 'package:web_socket_channel/web_socket_channel.dart';
import 'package:record/record.dart';
import 'dart:convert';
import 'dart:async';
import 'dart:typed_data';

class RecitationRemoteDataSource {
  final _audioRecorder = AudioRecorder();
  late WebSocketChannel _channel;
  final String baseUrl;
  
  StreamSubscription<Uint8List>? _micStreamSubscription;

  RecitationRemoteDataSource(this.baseUrl);

  // We connect to the WebSocket and start streaming immediately
  Future<Stream<dynamic>> startStreamingRecording(int surahId, int ayahId) async {
    if (!await _audioRecorder.hasPermission()) {
      throw Exception("Microphone permission denied");
    }

    _channel = WebSocketChannel.connect(Uri.parse('$baseUrl/ws/recite'));

    // 1. Send the identifying target Surah/Ayah metadata string 
    _channel.sink.add(jsonEncode({"surah_id": surahId, "ayah_id": ayahId}));

    // 2. Open a continuous stream from the microphone (16-bit PCM)
    final audioStream = await _audioRecorder.startStream(
      const RecordConfig(
        encoder: AudioEncoder.pcm16bits, 
        sampleRate: 16000, 
        numChannels: 1
      ),
    );

    // 3. Pipe the microphone bytes directly into the WebSocket
    _micStreamSubscription = audioStream.listen((data) {
      _channel.sink.add(data);
    });

    // 4. Return the WebSocket stream so the Presentation layer can listen for model responses
    return _channel.stream;
  }

  Future<void> stopStreaming() async {
    await _micStreamSubscription?.cancel();
    await _audioRecorder.stop();
    await _channel.sink.close();
  }
}