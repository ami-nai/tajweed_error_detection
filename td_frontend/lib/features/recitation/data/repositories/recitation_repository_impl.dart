import 'dart:async';
import '../../domain/repositories/recitation_repository.dart';
import '../datasources/recitation_remote_datasource.dart';

class RecitationRepositoryImpl implements RecitationRepository {
  final RecitationRemoteDataSource remoteDataSource;

  RecitationRepositoryImpl(this.remoteDataSource);

  @override
  Future<Stream<dynamic>> startStreaming(
    int surahId,
    int? ayahId, {
    String mode = 'single',
  }) async {
    return await remoteDataSource.startStreamingRecording(surahId, ayahId, mode: mode);
  }

  @override
  Future<void> nextAyah() async {
    await remoteDataSource.nextAyah();
  }

  @override
  Future<void> stopStreaming() async {
    await remoteDataSource.stopStreaming();
  }
}