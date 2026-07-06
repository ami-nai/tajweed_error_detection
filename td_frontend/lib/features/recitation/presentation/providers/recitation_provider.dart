import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_riverpod/legacy.dart';
import '../../data/datasources/recitation_remote_datasource.dart';
import '../../data/repositories/recitation_repository_impl.dart';
import '../../domain/entities/recitation_result.dart';
import '../../domain/repositories/recitation_repository.dart';
import 'dart:async';
import 'dart:convert';

final dataSourceProvider = Provider((ref) {
  // IMPORTANT: For a physical device, replace '192.168.x.x' below with your 
  // computer's local IP address (e.g., 'ws://192.168.1.10:8000')
  return RecitationRemoteDataSource('ws://192.168.1.111:8000'); 
});

final repositoryProvider = Provider<RecitationRepository>((ref) {
  return RecitationRepositoryImpl(ref.watch(dataSourceProvider));
});

class RecitationNotifier extends StateNotifier<RecitationResult> {
  final RecitationRepository _repository;
  StreamSubscription? _serverSubscription;

  RecitationNotifier(this._repository) : super(RecitationResult(status: RecitationStatus.idle));

  Future<void> startReciting(int surahId, int ayahId) async {
    try {
      state = RecitationResult(status: RecitationStatus.recording);
      
      // Clean Architecture: Call the repository, not the data source directly
      final serverStream = await _repository.startStreaming(surahId, ayahId);
      
      _serverSubscription = serverStream.listen((event) {
        final data = jsonDecode(event);
        
        RecitationStatus newStatus;
        switch(data['status']) {
          case 'advance': newStatus = RecitationStatus.success; break;
          case 'retry': newStatus = RecitationStatus.retry; break;
          default: newStatus = RecitationStatus.error;
        }

        state = RecitationResult(
          status: newStatus,
          accuracy: data['accuracy'],
          rawText: data['raw_text'],
          expectedPhonemes: data['expected_phonemes'],
          predictedPhonemes: data['predicted_phonemes'],
        );
      });
      
    } catch (e) {
      state = RecitationResult(status: RecitationStatus.error);
    }
  }

  Future<void> stopReciting() async {
    await _serverSubscription?.cancel();
    await _repository.stopStreaming();
    state = RecitationResult(status: RecitationStatus.idle);
  }
}

final recitationProvider = StateNotifierProvider<RecitationNotifier, RecitationResult>((ref) {
  return RecitationNotifier(ref.watch(repositoryProvider));
});