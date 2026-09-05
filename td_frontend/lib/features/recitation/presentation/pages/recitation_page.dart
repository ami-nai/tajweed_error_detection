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

    final bool isSurahMode = recitationState.mode == RecitationMode.surah;

    // Dynamic Ayah boundary lookup based on current selection configurations
    final int maxAyahs = recitationState.selectedSurah == 111
        ? 5
        : recitationState.selectedSurah == 112
            ? 4
            : recitationState.selectedSurah == 113
                ? 5
                : 6;

    return Scaffold(
      appBar: AppBar(
        title: const Text('Quran Recitation Tracker'),
        centerTitle: true,
      ),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(20.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            // Mode Toggle: Single Ayah / Full Surah
            Card(
              elevation: 2,
              shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16.0, vertical: 8.0),
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    ChoiceChip(
                      label: const Text('Single Ayah'),
                      selected: !isSurahMode,
                      onSelected: recitationState.status == RecitationStatus.recording
                          ? null
                          : (_) => notifier.setMode(RecitationMode.singleAyah),
                    ),
                    const SizedBox(width: 12),
                    ChoiceChip(
                      label: const Text('Full Surah'),
                      selected: isSurahMode,
                      onSelected: recitationState.status == RecitationStatus.recording
                          ? null
                          : (_) => notifier.setMode(RecitationMode.surah),
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 8),

            // 1. Selector Layout Area (Surah + optional Ayah dropdown)
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
                    if (!isSurahMode)
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

            // 2. The Quran Text Card
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
                child: isSurahMode
                    ? _buildSurahView(recitationState)
                    : _buildSingleAyahWrap(recitationState.words),
              ),
            ),
            const SizedBox(height: 20),

            // 3. Results Card
            Card(
              elevation: 2,
              shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
              child: Padding(
                padding: const EdgeInsets.all(16.0),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    if (isSurahMode) ...[
                      Text(
                        'Surah Average Accuracy: ${recitationState.surahAverage.toStringAsFixed(1)}%',
                        style: TextStyle(
                          fontSize: 20,
                          fontWeight: FontWeight.bold,
                          color: recitationState.surahAverage >= 70
                              ? Colors.teal
                              : recitationState.surahAverage >= 40
                                  ? Colors.orange
                                  : Colors.redAccent,
                        ),
                      ),
                      const SizedBox(height: 6),
                      if (recitationState.surahWER > 0) ...[
                        Text(
                          'Surah WER: ${recitationState.surahWER.toStringAsFixed(1)}%',
                          style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600, color: Colors.black54),
                        ),
                        const SizedBox(height: 6),
                      ],
                      if (recitationState.currentAyah != 0)
                        Text(
                          'Current Ayah: ${recitationState.currentAyah}',
                          style: const TextStyle(fontSize: 14, color: Colors.black54),
                        ),
                    ] else ...[
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
                      if (recitationState.wer > 0) ...[
                        const SizedBox(height: 6),
                        Text(
                          'WER: ${recitationState.wer.toStringAsFixed(1)}%',
                          style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600, color: Colors.black54),
                        ),
                      ],
                    ],
                    const SizedBox(height: 12),
                    Text('Real Transcription:',
                        style: TextStyle(fontWeight: FontWeight.bold, color: Colors.black54)),
                    const SizedBox(height: 4),
                    _buildDiffText(recitationState.diff, showExpected: true, empty: recitationState.expected),
                    const SizedBox(height: 12),
                    Text('Predicted Transcription:',
                        style: TextStyle(fontWeight: FontWeight.bold, color: Colors.black54)),
                    const SizedBox(height: 4),
                    _buildDiffText(recitationState.diff, showExpected: false, empty: recitationState.predicted),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 20),

            // Connection / Recitation status indicator
            if (recitationState.status == RecitationStatus.error)
              Container(
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: Colors.redAccent.withOpacity(0.1),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Text(
                  'Failed to connect to the server. Please make sure the backend is running at $kBackendUrl',
                  textAlign: TextAlign.center,
                  style: const TextStyle(color: Colors.redAccent, fontWeight: FontWeight.w600),
                ),
              )
            else if (recitationState.status == RecitationStatus.recording)
              Container(
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: Colors.teal.withOpacity(0.1),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Text(
                  isSurahMode ? 'Reciting Surah... wait for the pause between ayahs.' : 'Reciting...',
                  textAlign: TextAlign.center,
                  style: const TextStyle(color: Colors.teal, fontWeight: FontWeight.w600),
                ),
              ),

            const SizedBox(height: 20),

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
                  label: Text(
                    isSurahMode ? 'Start Surah' : 'Start',
                    style: const TextStyle(color: Colors.white, fontSize: 18),
                  ),
                  onPressed: recitationState.status == RecitationStatus.recording
                      ? null
                      : () => notifier.startReciting(
                            surahId: recitationState.selectedSurah,
                            ayahId: isSurahMode ? null : recitationState.selectedAyah,
                          ),
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

  Widget _buildSingleAyahWrap(List<WordTrackResult> words) {
    return Directionality(
      textDirection: TextDirection.rtl,
      child: Wrap(
        spacing: 12.0,
        runSpacing: 16.0,
        alignment: WrapAlignment.center,
        children: words.map((word) {
          return _wordChild(word);
        }).toList(),
      ),
    );
  }

  Widget _wordChild(WordTrackResult word) {
    final Color teal = Colors.teal;

    // Fallback: if no letter-level data is available, color the whole word.
    if (word.letters.isEmpty) {
      return _wordContainer(
        word.text,
        RichText(
          text: TextSpan(
            text: word.text,
            style: TextStyle(
              fontSize: 32,
              fontWeight: FontWeight.w600,
              fontFamily: 'Amiri',
              color: word.isRead ? teal : Colors.black87,
            ),
          ),
        ),
        word.isRead,
      );
    }

    return _wordContainer(
      word.text,
      Directionality(
        textDirection: TextDirection.rtl,
        child: RichText(
          textAlign: TextAlign.center,
          text: TextSpan(
            children: word.letters.map((letter) {
              final Color color = switch (letter.status) {
                LetterStatus.ok => teal,
                LetterStatus.miss => Colors.redAccent,
                LetterStatus.neutral => Colors.black87,
              };
              return TextSpan(
                text: letter.ch,
                style: TextStyle(
                  fontSize: 32,
                  fontWeight: FontWeight.w600,
                  fontFamily: 'Amiri',
                  color: color,
                ),
              );
            }).toList(),
          ),
        ),
      ),
      word.isRead,
    );
  }

  Widget _wordContainer(String semantics, Widget child, bool isRead) {
    return AnimatedContainer(
      duration: const Duration(milliseconds: 200),
      padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 2),
      decoration: BoxDecoration(
        color: isRead ? Colors.teal.withOpacity(0.1) : Colors.transparent,
        borderRadius: BorderRadius.circular(4),
      ),
      child: child,
    );
  }

  Widget _buildDiffText(List<DiffHit> diff, {required bool showExpected, required String empty}) {
    if (diff.isEmpty) {
      return Text(empty.isEmpty ? '—' : empty, style: const TextStyle(fontSize: 18, height: 1.4));
    }

    return Directionality(
      textDirection: TextDirection.rtl,
      child: RichText(
        textAlign: TextAlign.right,
        text: TextSpan(
          children: diff.map((d) {
            final String? ch = showExpected ? d.e : d.h;
            if (ch == null) return const TextSpan(text: '');
            final bool isSpace = ch.trim().isEmpty;
            Color color;
            if (isSpace) {
              color = Colors.black87;
            } else if (showExpected) {
              color = d.status == 'M' ? Colors.teal : Colors.redAccent;
            } else {
              color = (d.status == 'M' || d.status == 'D') ? Colors.teal : Colors.redAccent;
            }
            return TextSpan(
              text: ch,
              style: TextStyle(fontSize: 18, height: 1.4, color: color),
            );
          }).toList(),
        ),
      ),
    );
  }

  // Full-surah view: one labeled block per ayah, ordered by ayah number
  Widget _buildSurahView(RecitationResult state) {
    final ayahIds = state.surahWords.keys.toList()..sort();
    if (ayahIds.isEmpty) {
      return const Center(child: Text('No surah loaded'));
    }
    // The ayah available to read now: defaults to the first ayah until the
    // backend reports a next_ayah after the first pause.
    final readNow = state.nextAyah != 0 ? state.nextAyah : (ayahIds.isNotEmpty ? ayahIds.first : 0);
    final readingText = state.nextAyah != 0 ? '▶ Now read: Ayah ${state.nextAyah}' : '▶ Start reading: Ayah ${readNow}';
    return Column(
      children: [
        Container(
          margin: const EdgeInsets.only(bottom: 10),
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
          decoration: BoxDecoration(
            color: Colors.teal,
            borderRadius: BorderRadius.circular(8),
          ),
          child: Text(
            readingText,
            textAlign: TextAlign.center,
            style: const TextStyle(
              color: Colors.white,
              fontWeight: FontWeight.bold,
              fontSize: 16,
            ),
          ),
        ),
        ...ayahIds.map((aId) {
          final words = state.surahWords[aId] ?? [];
          final isCurrent = aId == readNow;
          final ayahAcc = state.ayahAccuracies[aId];
          final ayahWer = state.ayahWER[aId];
          return Container(
            margin: const EdgeInsets.symmetric(vertical: 6.0),
            padding: const EdgeInsets.all(10),
            decoration: BoxDecoration(
              color: isCurrent
                  ? Colors.teal.withOpacity(0.12)
                  : Colors.transparent,
              borderRadius: BorderRadius.circular(12),
              border: Border.all(
                color: isCurrent ? Colors.teal : Colors.black12,
                width: isCurrent ? 2 : 1,
              ),
            ),
            child: Directionality(
              textDirection: TextDirection.rtl,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  // Ayah label + (optional) per-ayah accuracy + WER + "read now" flag
                  Row(
                    mainAxisAlignment: MainAxisAlignment.end,
                    children: [
                      if (ayahWer != null && ayahWer > 0)
                        Text(
                          'WER ${ayahWer.toStringAsFixed(0)}%',
                          style: const TextStyle(
                              fontSize: 13, color: Colors.redAccent, fontWeight: FontWeight.w600),
                        ),
                      if (ayahAcc != null)
                        Text(
                          ' ${ayahAcc.toStringAsFixed(0)}%',
                          style: const TextStyle(
                              fontSize: 13, color: Colors.black45, fontWeight: FontWeight.w600),
                        ),
                      Text(
                        'Ayah $aId',
                        style: TextStyle(
                          fontSize: 13,
                          fontWeight: FontWeight.bold,
                          color: isCurrent ? Colors.teal : Colors.black54,
                        ),
                      ),
                      if (isCurrent) ...[
                        const SizedBox(width: 8),
                        Container(
                          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                          decoration: BoxDecoration(
                            color: Colors.teal,
                            borderRadius: BorderRadius.circular(8),
                          ),
                          child: const Text(
                            '▶ Read now',
                            style: TextStyle(fontSize: 12, color: Colors.white, fontWeight: FontWeight.bold),
                          ),
                        ),
                      ],
                    ],
                  ),
                  const SizedBox(height: 2),
                  Wrap(
                    spacing: 10.0,
                    runSpacing: 12.0,
                    alignment: WrapAlignment.end,
                    children: words.map((word) => _wordChild(word)).toList(),
                  ),
                ],
              ),
            ),
          );
        }),
      ],
    );
  }
}
