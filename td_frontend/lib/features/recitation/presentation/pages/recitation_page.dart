import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../providers/recitation_provider.dart';
import '../../domain/entities/recitation_result.dart';

class RecitationPage extends ConsumerWidget {
  const RecitationPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final recitationState = ref.watch(recitationProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('Tarteel Verification MVP')),
      body: Padding(
        padding: const EdgeInsets.all(20.0),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Text(
              'Target Ayah: لَمْ يَلِدْ وَلَمْ يُولَدْ',
              textAlign: TextAlign.center,
              style: TextStyle(fontSize: 22, fontWeight: FontWeight.bold),
            ),
            const SizedBox(height: 40),
            _buildStatusCard(recitationState),
            const SizedBox(height: 40),
            if (recitationState.status == RecitationStatus.recording)
              ElevatedButton.icon(
                style: ElevatedButton.styleFrom(backgroundColor: Colors.red, padding: const EdgeInsets.all(15)),
                icon: const Icon(Icons.stop, color: Colors.white),
                label: const Text('Stop Recitation', style: TextStyle(color: Colors.white, fontSize: 18)),
                onPressed: () => ref.read(recitationProvider.notifier).stopReciting(),
              )
            else if (recitationState.status == RecitationStatus.processing)
              const Center(child: CircularProgressIndicator())
            else
              ElevatedButton.icon(
                style: ElevatedButton.styleFrom(backgroundColor: Colors.green, padding: const EdgeInsets.all(15)),
                icon: const Icon(Icons.mic, color: Colors.white),
                label: const Text('Start Recitation', style: TextStyle(color: Colors.white, fontSize: 18)),
                onPressed: () => ref.read(recitationProvider.notifier).startReciting(112, 3), // Pass the IDs here
              ),
          ],
        ),
      ),
    );
  }

  Widget _buildStatusCard(RecitationResult state) {
    switch (state.status) {
      case RecitationStatus.recording:
        return const Card(
          color: Colors.amberAccent,
          child: Padding(
            padding: EdgeInsets.all(15.0),
            child: Text('Recording... Recite clearly now.', textAlign: TextAlign.center, style: TextStyle(color: Colors.black)),
          ),
        );
      case RecitationStatus.success:
        return Card(
          color: Colors.green.shade100,
          child: Padding(
            padding: const EdgeInsets.all(15.0),
            child: Column(
              children: [
                Text('Excellent! Correct Recitation ✔', style: TextStyle(color: Colors.green.shade900, fontWeight: FontWeight.bold)),
                Text('Accuracy: ${state.accuracy}', style: const TextStyle(color: Colors.black)),
                Text('Expected Phonemes: ${state.expectedPhonemes}', style: const TextStyle(fontSize: 12, color: Colors.black54)),
                Text('Predicted Phonemes: ${state.predictedPhonemes}', style: const TextStyle(fontSize: 12, color: Colors.black54)),
              ],
            ),
          ),
        );
      case RecitationStatus.retry:
      case RecitationStatus.error:
        return Card(
          color: Colors.red.shade100,
          child: Padding(
            padding: const EdgeInsets.all(15.0),
            child: Column(
              children: [
                Text('Pronunciation or Tajweed Error ❌', style: TextStyle(color: Colors.red.shade900, fontWeight: FontWeight.bold)),
                Text('Accuracy: ${state.accuracy ?? "0%"}', style: const TextStyle(color: Colors.black)),
                Text('Expected Phonemes: ${state.expectedPhonemes ?? ""}', style: const TextStyle(fontSize: 12, color: Colors.black54)),
                Text('Predicted Phonemes: ${state.predictedPhonemes ?? ""}', style: const TextStyle(fontSize: 12, color: Colors.black54)),
              ],
            ),
          ),
        );
      default:
        return const Card(
          child: Padding(
            padding: EdgeInsets.all(15.0),
            child: Text('Press the green button and read the Ayah out loud.', textAlign: TextAlign.center),
          ),
        );
    }
  }
}