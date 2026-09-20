// import 'package:web_socket_channel/web_socket_channel.dart';
// import 'package:record/record.dart';
// import 'dart:convert';
// import 'dart:async';
// import 'dart:typed_data';

// class RecitationRemoteDataSource {
//   final _audioRecorder = AudioRecorder();
//   late WebSocketChannel _channel;
//   final String baseUrl;
  
//   StreamSubscription<Uint8List>? _micStreamSubscription;

//   RecitationRemoteDataSource(this.baseUrl);

//   // We connect to the WebSocket and start streaming immediately
//   Future<Stream<dynamic>> startStreamingRecording(int surahId, int ayahId) async {
//     if (!await _audioRecorder.hasPermission()) {
//       throw Exception("Microphone permission denied");
//     }

//     _channel = WebSocketChannel.connect(Uri.parse('$baseUrl/ws/recite'));

//     // 1. Send the identifying target Surah/Ayah metadata string 
//     _channel.sink.add(jsonEncode({"surah_id": surahId, "ayah_id": ayahId}));

//     // 2. Open a continuous stream from the microphone (16-bit PCM)
//     final audioStream = await _audioRecorder.startStream(
//       const RecordConfig(
//         encoder: AudioEncoder.pcm16bits, 
//         sampleRate: 16000, 
//         numChannels: 1
//       ),
//     );

//     // 3. Pipe the microphone bytes directly into the WebSocket
//     _micStreamSubscription = audioStream.listen((data) {
//       _channel.sink.add(data);
//     });

//     // 4. Return the WebSocket stream so the Presentation layer can listen for model responses
//     return _channel.stream;
//   }

//   Future<void> stopStreaming() async {
//     await _micStreamSubscription?.cancel();
//     await _audioRecorder.stop();
//     await _channel.sink.close();
//   }
// }


import 'package:web_socket_channel/web_socket_channel.dart';
import 'package:record/record.dart';
import 'dart:convert';
import 'dart:async';
import 'dart:typed_data';

class RecitationRemoteDataSource {
  final _audioRecorder = AudioRecorder();
  WebSocketChannel? _channel; // Changed from late to nullable for safety
  final String baseUrl;
  
  StreamSubscription<Uint8List>? _micStreamSubscription;

  RecitationRemoteDataSource(this.baseUrl);

  // Connects to the WebSocket and starts streaming audio data chunks immediately.
  // ayahId is null for surah/open_mic modes; metadata carries the explicit mode
  // ('single' | 'surah' | 'open_mic'). For open_mic, surahId narrows candidates.
  Future<Stream<dynamic>> startStreamingRecording(
    int surahId,
    int? ayahId, {
    String mode = 'single',
  }) async {
    if (!await _audioRecorder.hasPermission()) {
      throw Exception("Microphone permission denied");
    }

    _channel = WebSocketChannel.connect(Uri.parse('$baseUrl/ws/recite'));

    // 1. Send the identifying target Surah metadata string (with explicit mode)
    final metadata = <String, dynamic>{"surah_id": surahId, "mode": mode};
    if (ayahId != null && mode == 'single') {
      metadata["ayah_id"] = ayahId;
    }
    _channel!.sink.add(jsonEncode(metadata));

    // 2. Open a continuous stream from the microphone (16-bit PCM)
    final audioStream = await _audioRecorder.startStream(
      const RecordConfig(
        encoder: AudioEncoder.pcm16bits,
        sampleRate: 16000,
        numChannels: 1,
      ),
    );

    // 3. Pipe the microphone bytes directly into the WebSocket
    _micStreamSubscription = audioStream.listen((data) {
      _channel?.sink.add(data);
    });

    // 4. Return the WebSocket stream so the presentation layer can listen for model responses
    return _channel!.stream;
  }

  Future<void> _sendControl(String type) async {
    try {
      _channel?.sink.add(jsonEncode({"type": type}));
    } catch (_) {}
  }

  // Surah mode: user finished the current ayah -> server scores it and advances.
  Future<void> nextAyah() => _sendControl('next');

  // Stops the mic and asks the server for the authoritative final result. The
  // channel is intentionally left open so the server's {final:true} message can
  // be delivered; the server closes it afterwards.
  Future<void> stopStreaming() async {
    await _micStreamSubscription?.cancel();
    await _audioRecorder.stop();
    await _sendControl('stop');
  }

  // Force-closes the channel (used as a fallback if the server never replies).
  Future<void> forceClose() async {
    await _channel?.sink.close();
  }

  // Tears down the mic AND the channel WITHOUT sending any control. Used when
  // the connection already broke (provider onError): a 'stop' here would make
  // the server finalize mid-recite. The Stop button is the only finalize path.
  Future<void> abortStreaming() async {
    await _micStreamSubscription?.cancel();
    _micStreamSubscription = null;
    try {
      await _audioRecorder.stop();
    } catch (_) {}
    await _channel?.sink.close();
  }
}