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
    final bool isOpenMic = recitationState.mode == RecitationMode.openMic;

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
                child: Wrap(
                  alignment: WrapAlignment.center,
                  spacing: 12,
                  runSpacing: 8,
                  children: [
                    ChoiceChip(
                      label: const Text('Single Ayah'),
                      selected: !isSurahMode && !isOpenMic,
                      onSelected: recitationState.status == RecitationStatus.recording
                          ? null
                          : (_) => notifier.setMode(RecitationMode.singleAyah),
                    ),
                    ChoiceChip(
                      label: const Text('Full Surah'),
                      selected: isSurahMode,
                      onSelected: recitationState.status == RecitationStatus.recording
                          ? null
                          : (_) => notifier.setMode(RecitationMode.surah),
                    ),
                    ChoiceChip(
                      label: const Text('Open Mic (Auto)'),
                      selected: isOpenMic,
                      onSelected: recitationState.status == RecitationStatus.recording
                          ? null
                          : (_) => notifier.setMode(RecitationMode.openMic),
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
                    if (!isSurahMode && !isOpenMic)
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
            const SizedBox(height: 16),

            // Start / Stop control buttons (below surah/ayah selection)
            Wrap(
              alignment: WrapAlignment.center,
              spacing: 12,
              runSpacing: 8,
              children: [
                ElevatedButton.icon(
                  style: ElevatedButton.styleFrom(
                    backgroundColor: Colors.teal,
                    padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
                    minimumSize: Size.zero,
                    tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                  ),
                  icon: const Icon(Icons.mic, color: Colors.white, size: 18),
                  label: Text(
                    isSurahMode
                        ? 'Start Surah'
                        : (isOpenMic ? 'Start Open Mic' : 'Start'),
                    style: const TextStyle(color: Colors.white, fontSize: 14),
                  ),
                  onPressed: recitationState.status == RecitationStatus.recording
                      ? null
                      : () => notifier.startReciting(
                            surahId: recitationState.selectedSurah,
                            ayahId: isSurahMode || isOpenMic ? null : recitationState.selectedAyah,
                          ),
                ),
                if (isSurahMode) ...[
                  ElevatedButton.icon(
                    style: ElevatedButton.styleFrom(
                      backgroundColor: Colors.indigo,
                      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
                      minimumSize: Size.zero,
                      tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                    ),
                    icon: const Icon(Icons.skip_next, color: Colors.white, size: 18),
                    label: const Text('Next', style: TextStyle(color: Colors.white, fontSize: 14)),
                    onPressed: recitationState.status != RecitationStatus.recording
                        ? null
                        : () => notifier.nextAyah(),
                  ),
                ],
                ElevatedButton.icon(
                  style: ElevatedButton.styleFrom(
                    backgroundColor: Colors.redAccent,
                    padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
                    minimumSize: Size.zero,
                    tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                  ),
                  icon: const Icon(Icons.stop, color: Colors.white, size: 18),
                  label: const Text('Stop', style: TextStyle(color: Colors.white, fontSize: 14)),
                  onPressed: recitationState.status != RecitationStatus.recording
                      ? null
                      : () => notifier.stopReciting(),
                ),
              ],
            ),
            const SizedBox(height: 16),

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
                    : (isOpenMic
                        ? _buildOpenMicCard(recitationState)
                        : _buildSingleAyahWrap(recitationState.words, recitationState.activeWordIndex)),
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
                      if (recitationState.surahPER > 0) ...[
                        Text(
                          'Surah PER: ${recitationState.surahPER.toStringAsFixed(1)}%',
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
                      if (recitationState.per > 0) ...[
                        const SizedBox(height: 6),
                        Text(
                          'PER: ${recitationState.per.toStringAsFixed(1)}%',
                          style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600, color: Colors.black54),
                        ),
                      ],
                    ],
                    const SizedBox(height: 12),
                    if (!isSurahMode) ...[
                      Text('Real Transcription:',
                          style: TextStyle(fontWeight: FontWeight.bold, color: Colors.black54)),
                      const SizedBox(height: 4),
                      _buildDiffText(recitationState.diff, showExpected: true, empty: recitationState.expected),
                      const SizedBox(height: 12),
                      Text('Predicted Transcription:',
                          style: TextStyle(fontWeight: FontWeight.bold, color: Colors.black54)),
                      const SizedBox(height: 4),
                      _buildDiffText(recitationState.diff, showExpected: false, empty: recitationState.predicted),
                      if (recitationState.mistakes.isNotEmpty) ...[
                        const SizedBox(height: 8),
                        _ExpandableMistakes(mistakes: recitationState.mistakes),
                      ],
                    ],
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
                  isSurahMode
                      ? 'Reciting Surah... tap Next after each ayah.'
                      : (isOpenMic
                          ? 'Listening... full surah shown; highlighting follows your recitation.'
                          : 'Reciting...'),
                  textAlign: TextAlign.center,
                  style: const TextStyle(color: Colors.teal, fontWeight: FontWeight.w600),
                ),
              ),

            const SizedBox(height: 20),
          ],
        ),
      ),
    );
  }

  Widget _buildSingleAyahWrap(List<WordTrackResult> words, int? activeIndex) {
    return Directionality(
      textDirection: TextDirection.rtl,
      child: Wrap(
        spacing: 12.0,
        runSpacing: 16.0,
        alignment: WrapAlignment.center,
        children: words.asMap().entries.map((entry) {
          return _wordChild(entry.value, isActive: entry.key == activeIndex);
        }).toList(),
      ),
    );
  }

  // Open-mic view: the full surah rendered as ONE continuous word flow (no
  // per-ayah boxes/chips) with a small ayah-end marker, plus the live raw
  // phoneme stream underneath.
  Widget _buildOpenMicCard(RecitationResult state) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _buildOpenMicFlow(state),
        const SizedBox(height: 12),
        const Divider(height: 1),
        const SizedBox(height: 8),
        const Text(
          'Live Predicted Phonemes:',
          style: TextStyle(fontWeight: FontWeight.bold, color: Colors.black54),
        ),
        const SizedBox(height: 4),
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
          decoration: BoxDecoration(
            color: Colors.black.withValues(alpha: 0.05),
            borderRadius: BorderRadius.circular(8),
          ),
          child: Directionality(
            textDirection: TextDirection.rtl,
            child: SelectableText(
              state.livePhonemes.isEmpty ? '—' : state.livePhonemes,
              textAlign: TextAlign.right,
              style: const TextStyle(fontSize: 18, height: 1.4, fontFamily: 'Amiri'),
            ),
          ),
        ),
      ],
    );
  }

  Widget _buildOpenMicFlow(RecitationResult state) {
    final ayahIds = state.surahWords.keys.toList()..sort();
    if (ayahIds.isEmpty) {
      return const Center(child: Text('Listening... will show the surah once detected'));
    }
    final children = <Widget>[];
    for (final aId in ayahIds) {
      final words = state.surahWords[aId] ?? [];
      final isDetectedAyah = aId == state.openMicAyah;
      for (var i = 0; i < words.length; i++) {
        children.add(_wordChild(
          words[i],
          isActive: isDetectedAyah && i == state.activeWordIndex,
        ));
      }
      children.add(_ayahEndMarker());
    }
    return Directionality(
      textDirection: TextDirection.rtl,
      child: Wrap(
        spacing: 12.0,
        runSpacing: 16.0,
        alignment: WrapAlignment.center,
        children: children,
      ),
    );
  }

  Widget _ayahEndMarker() {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 4),
      child: Text(
        '۝',
        style: const TextStyle(
          fontSize: 26,
          color: Colors.teal,
          fontWeight: FontWeight.bold,
          height: 1.0,
        ),
      ),
    );
  }

  Widget _wordChild(WordTrackResult word, {bool isActive = false}) {
    final Color teal = Colors.teal;

    // Fallback: if no letter-level data is available, color the whole word.
    if (word.letters.isEmpty) {
      return _wordContainer(
        word.text,
        RichText(
          text: TextSpan(
            text: word.text,
            style: TextStyle(
              fontSize: 24,
              fontWeight: FontWeight.w600,
              fontFamily: 'Amiri',
              color: word.isRead ? teal : Colors.black87,
            ),
          ),
        ),
        word.isRead,
        isActive: isActive,
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
                  fontSize: 24,
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
      isActive: isActive,
    );
  }

  Widget _wordContainer(String semantics, Widget child, bool isRead, {bool isActive = false}) {
    return AnimatedContainer(
      duration: const Duration(milliseconds: 200),
      padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 2),
      decoration: BoxDecoration(
        color: isRead
            ? Colors.teal.withOpacity(0.1)
            : (isActive ? Colors.amber.withOpacity(0.25) : Colors.transparent),
        borderRadius: BorderRadius.circular(4),
        border: (isActive && !isRead)
            ? Border.all(color: Colors.amber.shade700, width: 1.5)
            : null,
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

  // Full-surah view: one labeled block per ayah, ordered by ayah number. In
  // surah mode the current ayah is driven by nextAyah; in open-mic mode it
  // follows the server's live detection (openMicAyah) and highlights words as
  // they are recited.
  Widget _buildSurahView(RecitationResult state) {
    final ayahIds = state.surahWords.keys.toList()..sort();
    if (ayahIds.isEmpty) {
      return const Center(child: Text('No surah loaded'));
    }
    final bool isOpenMic = state.mode == RecitationMode.openMic;
    final int? currentAyah;
    final String headerText;
    if (isOpenMic) {
      currentAyah = state.openMicAyah;
      headerText = currentAyah == null
          ? 'Listening... auto-detecting your ayah'
          : '▶ Now reading: Ayah $currentAyah';
    } else {
      // The ayah available to read now: defaults to the first ayah until the
      // backend reports a next_ayah after the first pause.
      final readNow = state.nextAyah != 0 ? state.nextAyah : ayahIds.first;
      currentAyah = readNow;
      headerText = state.nextAyah != 0 ? '▶ Now read: Ayah ${state.nextAyah}' : '▶ Start reading: Ayah $readNow';
    }
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
            headerText,
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
          final isCurrent = aId == currentAyah;
          final ayahAcc = state.ayahAccuracies[aId];
          final ayahPer = state.ayahPER[aId];
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
                  // Ayah label + (optional) per-ayah accuracy + PER + "read now" flag
                  Directionality(
                    textDirection: TextDirection.ltr,
                    child: Row(
                      children: [
                        Text(
                          'Ayah $aId',
                          style: TextStyle(
                            fontSize: 11,
                            fontWeight: FontWeight.bold,
                            color: isCurrent ? Colors.teal : Colors.black54,
                          ),
                        ),
                        const Spacer(),
                        if (isCurrent) ...[
                          Container(
                            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                            decoration: BoxDecoration(
                              color: Colors.teal,
                              borderRadius: BorderRadius.circular(8),
                            ),
                            child: Text(
                              isOpenMic ? '▶ Reading' : '▶ Read now',
                              style: const TextStyle(fontSize: 10, color: Colors.white, fontWeight: FontWeight.bold),
                            ),
                          ),
                          const SizedBox(width: 8),
                        ],
                        if (ayahAcc != null) ...[
                          Text(
                            'Acc ${ayahAcc.toStringAsFixed(0)}%',
                            style: const TextStyle(
                                fontSize: 11, color: Colors.black45, fontWeight: FontWeight.w600),
                          ),
                          const SizedBox(width: 8),
                        ],
                        if (ayahPer != null && ayahPer > 0)
                          Text(
                            'PER ${ayahPer.toStringAsFixed(0)}%',
                            style: const TextStyle(
                                fontSize: 11, color: Colors.redAccent, fontWeight: FontWeight.w600),
                          ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 2),
                  Wrap(
                    spacing: 10.0,
                    runSpacing: 12.0,
                    alignment: WrapAlignment.end,
                    children: words.asMap().entries.map((entry) {
                      return _wordChild(
                        entry.value,
                        isActive: isCurrent && entry.key == state.activeWordIndex,
                      );
                    }).toList(),
                  ),
                  if (state.ayahDiffs.containsKey(aId)) ...[
                    const SizedBox(height: 4),
                    _ExpandableTranscription(
                      diff: state.ayahDiffs[aId] ?? const [],
                      builder: (diff, showExpected) => _buildDiffText(
                        diff,
                        showExpected: showExpected,
                        empty: showExpected
                            ? (state.ayahExpected[aId] ?? '')
                            : (state.ayahPredicted[aId] ?? ''),
                      ),
                    ),
                  ],
                  if ((state.ayahMistakes[aId] ?? const []).isNotEmpty) ...[
                    const SizedBox(height: 4),
                    _ExpandableMistakes(mistakes: state.ayahMistakes[aId] ?? const []),
                  ],
                ],
              ),
            ),
          );
        }),
      ],
    );
  }
}

class _ExpandableTranscription extends StatefulWidget {
  final List<DiffHit> diff;
  final Widget Function(List<DiffHit> diff, bool showExpected) builder;

  const _ExpandableTranscription({required this.diff, required this.builder});

  @override
  State<_ExpandableTranscription> createState() => _ExpandableTranscriptionState();
}

class _ExpandableTranscriptionState extends State<_ExpandableTranscription> {
  bool _expanded = false;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: double.infinity,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.end,
        children: [
          InkWell(
            onTap: () => setState(() => _expanded = !_expanded),
            borderRadius: BorderRadius.circular(8),
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.end,
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(
                    _expanded ? 'Hide transcription' : 'Show transcription',
                    style: const TextStyle(fontSize: 12, color: Colors.black45),
                  ),
                  Icon(
                    _expanded ? Icons.arrow_drop_up : Icons.arrow_drop_down,
                    color: Colors.black45,
                    size: 22,
                  ),
                ],
              ),
            ),
          ),
          if (_expanded) ...[
            const Divider(height: 8),
            const Text('Real Transcription:',
                style: TextStyle(fontWeight: FontWeight.w600, color: Colors.black54)),
            const SizedBox(height: 2),
            widget.builder(widget.diff, true),
            const SizedBox(height: 8),
            const Text('Predicted Transcription:',
                style: TextStyle(fontWeight: FontWeight.w600, color: Colors.black54)),
            const SizedBox(height: 2),
            widget.builder(widget.diff, false),
          ],
        ],
      ),
    );
  }
}

class _ExpandableMistakes extends StatefulWidget {
  final List<String> mistakes;
  const _ExpandableMistakes({required this.mistakes});

  @override
  State<_ExpandableMistakes> createState() => _ExpandableMistakesState();
}

class _ExpandableMistakesState extends State<_ExpandableMistakes> {
  bool _expanded = false;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: double.infinity,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          InkWell(
            onTap: () => setState(() => _expanded = !_expanded),
            borderRadius: BorderRadius.circular(8),
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  const Icon(Icons.error_outline, color: Colors.redAccent, size: 16),
                  const SizedBox(width: 4),
                  Text(
                    _expanded ? 'Hide mistakes' : 'Show mistakes',
                    style: const TextStyle(fontSize: 12, color: Colors.redAccent),
                  ),
                  Icon(
                    _expanded ? Icons.arrow_drop_up : Icons.arrow_drop_down,
                    color: Colors.redAccent,
                    size: 22,
                  ),
                ],
              ),
            ),
          ),
          if (_expanded) ...[
            const Divider(height: 8),
            for (final msg in widget.mistakes)
              Padding(
                padding: const EdgeInsets.only(bottom: 4),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text('• ', style: TextStyle(color: Colors.redAccent, fontSize: 14)),
                    Expanded(
                      child: Text(
                        msg,
                        textDirection: TextDirection.ltr,
                        style: const TextStyle(fontSize: 13, color: Colors.black87, height: 1.3),
                      ),
                    ),
                  ],
                ),
              ),
          ],
        ],
      ),
    );
  }
}
