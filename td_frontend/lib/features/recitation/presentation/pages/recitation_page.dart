import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../providers/recitation_provider.dart';
import '../../domain/entities/recitation_result.dart';

class RecitationPage extends ConsumerWidget {
  const RecitationPage({Key? key}) : super(key: key);

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    // Watch everything from our central state system
    final recitationState = ref.watch(recitationProvider);
    final notifier = ref.read(recitationProvider.notifier);

    // Dynamic Ayah boundary lookup based on current selection configurations
    final int maxAyahs = switch (recitationState.selectedSurah) {
      111 => 5,
      112 => 4,
      113 => 5,
      114 => 6,
      _ => 4,
    };

    return Scaffold(
      appBar: AppBar(
        title: const Text('Quran Recitation Tracker'),
        centerTitle: true,
      ),
      body: Padding(
        padding: const EdgeInsets.all(20.0),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            // 1. Selector Layout Area
            Card(
              elevation: 2,
              shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16.0, vertical: 8.0),
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                  children: [
                    DropdownButton<int>(
                      value: recitationState.selectedSurah,
                      items: const [
                        DropdownMenuItem(value: 111, child: Text('Surah 111 (Al-Masad)')),
                        DropdownMenuItem(value: 112, child: Text('Surah 112 (Al-Ikhlas)')),
                        DropdownMenuItem(value: 113, child: Text('Surah 113 (Al-Falaq)')),
                        DropdownMenuItem(value: 114, child: Text('Surah 114 (An-Nas)')),
                      ],
                      // Block selection changes while recording is running
                      onChanged: recitationState.status == RecitationStatus.recording
                          ? null
                          : (value) {
                              if (value != null) notifier.updateSelection(value, 1);
                            },
                    ),
                    DropdownButton<int>(
                      value: recitationState.selectedAyah,
                      items: List.generate(maxAyahs, (index) => index + 1)
                          .map((ayahNum) => DropdownMenuItem(
                                value: ayahNum,
                                child: Text('Ayah $ayahNum'),
                              ))
                          .toList(),
                      onChanged: recitationState.status == RecitationStatus.recording
                          ? null
                          : (value) {
                              if (value != null) {
                                notifier.updateSelection(recitationState.selectedSurah, value);
                              }
                            },
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 20),

            // 2. The Quran Text Card (Now renders instantly!)
            Card(
              elevation: 4,
              shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(15)),
              child: Container(
                width: double.infinity,
                padding: const EdgeInsets.all(24.0),
                decoration: BoxDecoration(
                  color: Colors.amber.withOpacity(0.05),
                  borderRadius: BorderRadius.circular(15),
                ),
                child: Directionality(
                  textDirection: TextDirection.rtl, // Forces proper Arabic layout
                  child: Wrap(
                    spacing: 12.0,
                    runSpacing: 16.0,
                    alignment: WrapAlignment.center,
                    children: recitationState.words.map((word) {
                      return AnimatedContainer(
                        duration: const Duration(milliseconds: 200),
                        padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 2),
                        decoration: BoxDecoration(
                          color: word.isRead ? Colors.teal.withOpacity(0.1) : Colors.transparent,
                          borderRadius: BorderRadius.circular(4),
                        ),
                        child: Text(
                          word.text,
                          style: TextStyle(
                            fontSize: 32,
                            fontWeight: FontWeight.w600,
                            fontFamily: 'Amiri',
                            // Updates instantly from bold black to teal when recognized by backend streams
                            color: word.isRead ? Colors.teal : Colors.black87,
                          ),
                        ),
                      );
                    }).toList(),
                  ),
                ),
              ),
            ),
            const SizedBox(height: 20),

            // 3. Results Card: Accuracy + Real vs Predicted transcription
            Card(
              elevation: 2,
              shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
              child: Padding(
                padding: const EdgeInsets.all(16.0),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Text(
                      'Accuracy: ${recitationState.accuracy.toStringAsFixed(1)}%',
                      style: TextStyle(
                        fontSize: 22,
                        fontWeight: FontWeight.bold,
                        color: recitationState.accuracy >= 70
                            ? Colors.teal
                            : recitationState.accuracy >= 40
                                ? Colors.orange
                                : Colors.redAccent,
                      ),
                    ),
                    const SizedBox(height: 12),
                    Text('Real Transcription:',
                        style: TextStyle(fontWeight: FontWeight.bold, color: Colors.black54)),
                    const SizedBox(height: 4),
                    Text(
                      recitationState.expected.isEmpty
                          ? '—'
                          : recitationState.expected,
                      style: const TextStyle(fontSize: 18, height: 1.4),
                    ),
                    const SizedBox(height: 12),
                    Text('Predicted Transcription:',
                        style: TextStyle(fontWeight: FontWeight.bold, color: Colors.black54)),
                    const SizedBox(height: 4),
                    Text(
                      recitationState.predicted.isEmpty
                          ? '—'
                          : recitationState.predicted,
                      style: const TextStyle(fontSize: 18, height: 1.4),
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 40),

            // 4. Control Buttons
            Row(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                ElevatedButton.icon(
                  style: ElevatedButton.styleFrom(
                    backgroundColor: Colors.teal,
                    padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 12),
                  ),
                  icon: const Icon(Icons.mic, color: Colors.white),
                  label: const Text('Start', style: TextStyle(color: Colors.white, fontSize: 18)),
                  onPressed: recitationState.status == RecitationStatus.recording
                      ? null
                      : () => notifier.startReciting(recitationState.selectedSurah, recitationState.selectedAyah),
                ),
                const SizedBox(width: 20),
                ElevatedButton.icon(
                  style: ElevatedButton.styleFrom(
                    backgroundColor: Colors.redAccent,
                    padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 12),
                  ),
                  icon: const Icon(Icons.stop, color: Colors.white),
                  label: const Text('Stop', style: TextStyle(color: Colors.white, fontSize: 18)),
                  onPressed: recitationState.status != RecitationStatus.recording
                      ? null
                      : () => notifier.stopReciting(),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}