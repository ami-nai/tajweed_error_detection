import 'dart:async';
import '../../domain/repositories/recitation_repository.dart';
import '../datasources/recitation_remote_datasource.dart';

class RecitationRepositoryImpl implements RecitationRepository {
  final RecitationRemoteDataSource remoteDataSource;

  RecitationRepositoryImpl(this.remoteDataSource);

  @override
  Future<Stream<dynamic>> startStreaming(int surahId, int ayahId) async {
    return await remoteDataSource.startStreamingRecording(surahId, ayahId);
  }

  @override
  Future<void> stopStreaming() async {
    await remoteDataSource.stopStreaming();
  }
}