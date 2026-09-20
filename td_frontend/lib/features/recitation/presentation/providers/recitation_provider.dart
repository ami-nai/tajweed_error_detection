import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../data/datasources/recitation_remote_datasource.dart';
import '../../data/repositories/recitation_repository_impl.dart';
import '../../domain/entities/recitation_result.dart';
import '../../domain/repositories/recitation_repository.dart';
import 'dart:async';
import 'dart:convert';

final Map<int, Map<int, List<String>>> quranTextDatabase = {
  111: {
    1: ["تَبَّتْ", "يَدَا", "أَبِي", "لَهَبٍ", "وَتَبَّ"],
    2: ["مَا", "أَغْنَىٰ", "عَنْهُ", "مَالُهُ", "وَمَا", "كَسَبَ"],
    3: ["سَيَصْلَىٰ", "نَارًا", "ذَاتَ", "لَهَبٍ"],
    4: ["وَامْرَأَتُهُ", "حَمَّالَةَ", "الْحَطَبِ"],
    5: ["فِي", "جِيدِهَا", "حَبْلٌ", "مِّن", "مَّسَدٍ"],
  },
  112: {
    1: ["قُلْ", "هُوَ", "اللَّهُ", "أَحَدٌ"],
    2: ["اللَّهُ", "الصَّمَدُ"],
    3: ["لَمْ", "يَلِدْ", "وَلَمْ", "يُولَدْ"],
    4: ["وَلَمْ", "يَكُنْ", "لَهُ", "كُفُوًا", "أَحَدٌ"],
  },
  113: {
    1: ["قُلْ", "أَعُوذُ", "بِرَبِّ", "الْفَلَقِ"],
    2: ["مِن", "شَرِّ", "مَا", "خَلَقَ"],
    3: ["وَمِن", "شَرِّ", "غَاسِقٍ", "إِذَا", "وَقَبَ"],
    4: ["وَمِن", "شَرِّ", "النَّفَّاثَاتِ", "فِي", "الْعُقَدِ"],
    5: ["وَمِن", "شَرِّ", "حَاسِدٍ", "إِذَا", "حَسَدَ"],
  },
  114: {
    1: ["قُلْ", "أَعُوذُ", "بِرَبِّ", "النَّاسِ"],
    2: ["مَلِكِ", "النَّاسِ"],
    3: ["إِلَٰهِ", "النَّاسِ"],
    4: ["مِن", "شَرِّ", "الْوَسْوَاسِ", "الْخَنَّاسِ"],
    5: ["الَّذِي", "يُوَسْوِسُ", "فِي", "صُدُورِ", "النَّاسِ"],
    6: ["مِنَ", "الْجِنَّةِ", "وَالنَّاسِ"],
  }
};

int maxAyahsForSurah(int surahId) {
  return quranTextDatabase[surahId]?.length ?? 0;
}

// Backend WebSocket base URL. Override at build time with:
//   flutter build apk --dart-define=BACKEND_URL=wss://your-host.example
// Defaults to the local dev server.
const String kBackendUrl = String.fromEnvironment(
  'BACKEND_URL',
  defaultValue: 'ws://192.168.1.113:8000',
);

List<DiffHit> _parseDiff(Object? diff) {
  if (diff is! List) return [];
  return diff.map((e) {
    if (e is Map) {
      return DiffHit.fromJson(e.cast<String, dynamic>());
    }
    return DiffHit(status: 'S');
  }).toList();
}

List<WordTrackResult> _parseServerWords(Object? wordsRaw) {
  if (wordsRaw is! List) return [];
  return wordsRaw.map((item) {
    if (item is Map) {
      final m = item.cast<String, dynamic>();
      final letters = (m['letters'] as List?)?.map((l) {
        if (l is Map) {
          return LetterHit.fromJson(l.cast<String, dynamic>());
        }
        return LetterHit(ch: l.toString(), status: LetterStatus.neutral);
      }).toList() ?? const <LetterHit>[];
      return WordTrackResult(
        text: m['text']?.toString() ?? '',
        isRead: m['is_read'] == true || m['isRead'] == true,
        letters: letters,
      );
    }
    return WordTrackResult(text: item.toString(), isRead: false);
  }).toList();
}

final dataSourceProvider = Provider((ref) {
  return RecitationRemoteDataSource(kBackendUrl);
});

final repositoryProvider = Provider<RecitationRepository>((ref) {
  return RecitationRepositoryImpl(ref.watch(dataSourceProvider));
});

class RecitationNotifier extends Notifier<RecitationResult> {
  StreamSubscription? _serverSubscription;

  @override
  RecitationResult build() {
    ref.onDispose(() {
      _serverSubscription?.cancel();
    });

    return RecitationResult(
      words: quranTextDatabase[112]![1]!.map((w) => WordTrackResult(text: w, isRead: false)).toList(),
      status: RecitationStatus.idle,
      selectedSurah: 112,
      selectedAyah: 1,
    );
  }

  String _normalizeArabic(String text) {
    final RegExp diacritics = RegExp(r'[\u064B-\u065F\u0670\u0654]');
    String normalized = text.replaceAll(diacritics, '');
    normalized = normalized.replaceAll(RegExp(r'[إأآا]'), 'ا');
    normalized = normalized.replaceAll(RegExp(r'[ىي]'), 'ي');
    normalized = normalized.replaceAll(RegExp(r'[ةه]'), 'ه');
    return normalized.trim();
  }

  // Seed the full-surah word map (all ayahs, unread) from the local Quran text.
  Map<int, List<WordTrackResult>> _seedSurahWords(int surahId) {
    final ayahs = quranTextDatabase[surahId] ?? {};
    final out = <int, List<WordTrackResult>>{};
    for (final entry in ayahs.entries) {
      out[entry.key] = entry.value
          .map((w) => WordTrackResult(text: w, isRead: false))
          .toList();
    }
    return out;
  }

  void _clearAyahMetrics() {
    state = state.copyWith(
      ayahAccuracies: {},
      surahAverage: 0,
      currentAyah: 0,
      nextAyah: 0,
      ayahPER: {},
      surahPER: 0,
      diff: [],
      ayahDiffs: {},
      ayahExpected: {},
      ayahPredicted: {},
      ayahMistakes: {},
    );
  }

  void setMode(RecitationMode mode) {
    if (mode == state.mode) return;
    if (mode == RecitationMode.singleAyah) {
      _loadSingleAyah(state.selectedSurah, state.selectedAyah);
    } else if (mode == RecitationMode.surah) {
      _loadSurah(state.selectedSurah);
    } else {
      state = state.copyWith(
        mode: mode,
        status: RecitationStatus.idle,
        words: [],
        openMicAyah: null,
        livePhonemes: '',
        surahWords: _seedSurahWords(state.selectedSurah),
        activeWordIndex: null,
      );
      _clearAyahMetrics();
    }
  }

  void updateSelection(int surahId, int ayahId) {
    if (state.mode == RecitationMode.singleAyah) {
      _loadSingleAyah(surahId, ayahId);
    } else if (state.mode == RecitationMode.surah) {
      _loadSurah(surahId);
    } else {
      // Open Mic: surah is only a candidate filter; the full surah is shown and
      // words get highlighted once the server detects the recited ayah.
      state = state.copyWith(
        selectedSurah: surahId,
        selectedAyah: 1,
        words: [],
        openMicAyah: null,
        livePhonemes: '',
        surahWords: _seedSurahWords(surahId),
        activeWordIndex: null,
      );
      _clearAyahMetrics();
    }
  }

  void _loadSingleAyah(int surahId, int ayahId) {
    final staticWords = quranTextDatabase[surahId]?[ayahId] ?? [];
    state = state.copyWith(
      selectedSurah: surahId,
      selectedAyah: ayahId,
      status: RecitationStatus.idle,
      mode: RecitationMode.singleAyah,
      words: staticWords.map((w) => WordTrackResult(text: w, isRead: false)).toList(),
      activeWordIndex: 0,
    );
  }

  void _loadSurah(int surahId) {
    final ayahs = quranTextDatabase[surahId] ?? {};
    final surahWords = <int, List<WordTrackResult>>{};
    for (final entry in ayahs.entries) {
      surahWords[entry.key] = entry.value
          .map((w) => WordTrackResult(text: w, isRead: false))
          .toList();
    }
    state = state.copyWith(
      selectedSurah: surahId,
      status: RecitationStatus.idle,
      mode: RecitationMode.surah,
      surahWords: surahWords,
      ayahAccuracies: {},
      surahAverage: 0,
      currentAyah: 0,
      nextAyah: 0,
      words: [],
      ayahPER: {},
      surahPER: 0,
      diff: [],
      activeWordIndex: 0,
    );
  }

  Future<void> startReciting({required int surahId, int? ayahId}) async {
    try {
      await _serverSubscription?.cancel();

      final modeStr = switch (state.mode) {
        RecitationMode.surah => 'surah',
        RecitationMode.openMic => 'open_mic',
        RecitationMode.singleAyah => 'single',
      };

      if (state.mode == RecitationMode.singleAyah && ayahId != null) {
        final staticWords = quranTextDatabase[surahId]?[ayahId] ?? [];
        state = state.copyWith(
          status: RecitationStatus.recording,
          words: staticWords.map((w) => WordTrackResult(text: w, isRead: false)).toList(),
          activeWordIndex: 0,
        );
      } else if (state.mode == RecitationMode.surah) {
        _loadSurah(surahId);
        state = state.copyWith(status: RecitationStatus.recording, activeWordIndex: 0);
      } else {
        // Open Mic: words are seeded once the server detects an ayah; the full
        // surah is already on screen and highlighting follows detection.
        state = state.copyWith(
          status: RecitationStatus.recording,
          words: [],
          openMicAyah: null,
          livePhonemes: '',
          surahWords: _seedSurahWords(surahId),
          activeWordIndex: null,
        );
        _clearAyahMetrics();
      }

      print("🔵 [RECITATION PROV] Preparing stream for Surah: $surahId, mode: ${state.mode}");
      final repository = ref.read(repositoryProvider);
      final serverStream = await repository.startStreaming(surahId, ayahId, mode: modeStr);
      print("🟢 [RECITATION PROV] Stream connection successfully initiated!");

      _serverSubscription = serverStream.listen(
        (event) {
          print("📥 [SERVER EVENT RECEIVED]: $event");
          try {
            final Map<String, dynamic> data = jsonDecode(event);
            _handleServerPayload(data);
          } catch (payloadError) {
            // A single malformed message must never tear down the stream.
            print("❌ [PAYLOAD ERROR] Ignoring malformed server message: $payloadError");
          }
        },
        onError: (error) {
          print("❌ [STREAM ERROR] The server stream emitted an error: $error");
          state = state.copyWith(
            status: RecitationStatus.error,
            activeWordIndex: null,
          );
          // Tear down the broken channel WITHOUT sending a 'stop' (a 'stop' here
          // would finalize mid-recite). The Stop button is the only finalize path.
          try {
            ref.read(dataSourceProvider).abortStreaming();
          } catch (teardownError) {
            print("❌ [TEARDOWN ERROR] abortStreaming failed: $teardownError");
          }
        },
        onDone: () {
          print("🔌 [STREAM CLOSED] The server connection closed down automatically (onDone).");
          state = state.copyWith(
            status: RecitationStatus.idle,
            activeWordIndex: null,
          );
        }
      );
    } catch (e) {
      print("❌ [CRITICAL FAILURE] Failed during startReciting initiation sequence: $e");
      state = state.copyWith(status: RecitationStatus.error);
    }
  }

  void _handleServerPayload(Map<String, dynamic> data) {
    final mode = data['mode']?.toString() ?? 'single';
    final bool isFinal = data['final'] == true;

    if (mode == 'surah') {
      // Fast "advance" message (legacy): immediately tell which ayah to read next.
      final readNow = data['read_now'];
      if (data['advance'] == true) {
        final isFinished = data['finished'] == true;
        state = state.copyWith(
          status: isFinished ? RecitationStatus.success : RecitationStatus.recording,
          nextAyah: readNow is num ? readNow.toInt() : state.nextAyah,
        );
        print("👁 [SURAH ADVANCE] read_now=${state.nextAyah} finished=$isFinished");
        return;
      }

      // Sequential surah mode (interim final:false messages and next/stop finals).
      final surahWords = state.surahWords;
      final finalAyahId = (data['current_ayah'] as num?)?.toInt() ?? state.currentAyah;

      // Update word highlights from the per-ayah word lists (order = ayah_order)
      final wordsList = data['words'];
      final ayahOrder = (data['ayah_order'] as List?)?.map((e) => (e as num).toInt()).toList();
      final updated = <int, List<WordTrackResult>>{};
      if (wordsList is List && ayahOrder != null) {
        for (var i = 0; i < ayahOrder.length && i < wordsList.length; i++) {
          final aId = ayahOrder[i];
          final items = (wordsList[i] as List).map((item) {
            if (item is Map) {
              return WordTrackResult(
                text: item['text']?.toString() ?? '',
                isRead: item['is_read'] == true || item['isRead'] == true,
                letters: (item['letters'] as List?)?.map((l) {
                  if (l is Map) {
                    return LetterHit.fromJson(l.cast<String, dynamic>());
                  }
                  return LetterHit(ch: l.toString(), status: LetterStatus.neutral);
                }).toList(),
              );
            }
            return WordTrackResult(text: item.toString(), isRead: false);
          }).toList();
          updated[aId] = items;
        }
      }
      // Preserve any missing ayahs (in case order is incomplete)
      for (final e in surahWords.entries) {
        updated.putIfAbsent(e.key, () => e.value);
      }

      // Accuracies (seed from existing so a bare 'finished' message doesn't wipe them)
      final ayahAcc = <int, double>{...state.ayahAccuracies};
      final accData = data['ayah_accuracies'];
      if (accData is Map) {
        accData.forEach((k, v) {
          if (v != null) ayahAcc[int.parse(k.toString())] = (v as num).toDouble();
        });
      }

      // PER per ayah
      final ayahPER = <int, double>{...state.ayahPER};
      final perData = data['ayah_pers'];
      if (perData is Map) {
        perData.forEach((k, v) {
          if (v != null) ayahPER[int.parse(k.toString())] = (v as num).toDouble();
        });
      }

      final isFinished = data['finished'] == true;
      final nextAyah = (data['next_ayah'] as num?)?.toInt() ?? 0;
      // active_index only rides on interim payloads (_send_interim); final
      // messages (do_next/do_stop) carry none, so a finalized ayah clears
      // the spotlight rather than leaving it stuck on a stale index.
      final rawActive = data['active_index'];
      final int? activeIdx = isFinal
          ? null
          : (rawActive is num ? rawActive.toInt() : state.activeWordIndex);
      // Per-ayah transcriptions (only update when the message carries them).
      final ayahDiffs = Map<int, List<DiffHit>>.from(state.ayahDiffs);
      final ayahExpected = Map<int, String>.from(state.ayahExpected);
      final ayahPredicted = Map<int, String>.from(state.ayahPredicted);
      final ayahMistakes = Map<int, List<String>>.from(state.ayahMistakes);
      final hasTranscript = data.containsKey('expected') && data.containsKey('predicted');
      if (hasTranscript) {
        final diffList = _parseDiff(data['diff']);
        ayahDiffs[finalAyahId] = diffList;
        ayahExpected[finalAyahId] = data['expected']?.toString() ?? '';
        ayahPredicted[finalAyahId] = data['predicted']?.toString() ?? '';
        if (data['mistakes'] is List) {
          ayahMistakes[finalAyahId] =
              (data['mistakes'] as List).map((e) => e.toString()).toList();
        }
      }
      state = state.copyWith(
        status: isFinished ? RecitationStatus.success : RecitationStatus.recording,
        words: [],
        surahWords: updated,
        ayahAccuracies: ayahAcc,
        surahAverage: (data['surah_average'] as num?)?.toDouble() ?? state.surahAverage,
        currentAyah: finalAyahId,
        nextAyah: nextAyah,
        ayahPER: ayahPER,
        surahPER: (data['surah_per'] as num?)?.toDouble() ?? state.surahPER,
        diff: data.containsKey('diff') ? _parseDiff(data['diff']) : state.diff,
        ayahDiffs: ayahDiffs,
        ayahExpected: ayahExpected,
        ayahPredicted: ayahPredicted,
        ayahMistakes: ayahMistakes,
        activeWordIndex: activeIdx,
      );
      print("📈 [SURAH STATE] avg=${state.surahAverage} current=${state.currentAyah}");
    } else {
      // Single-ayah / open-mic mode.
      final isOpenMic = data['mode']?.toString() == 'open_mic';

      // Open-mic detection reflects the detected target in the UI selection.
      int? detectedAyah;
      final detected = data['detected'];
      if (detected is Map) {
        final m = detected.cast<String, dynamic>();
        final s = (m['surah_id'] as num).toInt();
        final a = (m['ayah_id'] as num).toInt();
        detectedAyah = a;
        state = state.copyWith(
          selectedSurah: s,
          selectedAyah: a,
          // If detection landed on a different surah than the seeded one,
          // reseed the full-surah display for it.
          surahWords: s != state.selectedSurah ? _seedSurahWords(s) : state.surahWords,
        );
        print("🎯 [OPEN MIC] Server detected surah=$s ayah=$a");
      }

      final serverWords = _parseServerWords(data['words']);
      final liveStream = data['live']?.toString();

      if (isFinal) {
        // Authoritative result: REPLACE word state (and metrics) wholesale.
        final replaced = serverWords.isNotEmpty ? serverWords : state.words;
        final finalAyah = detectedAyah ?? state.openMicAyah ?? state.selectedAyah;
        final finalSurahWords = Map<int, List<WordTrackResult>>.from(state.surahWords);
        if (replaced.isNotEmpty) finalSurahWords[finalAyah] = replaced;
        state = state.copyWith(
          status: RecitationStatus.success,
          words: replaced,
          openMicAyah: detectedAyah ?? state.openMicAyah,
          surahWords: finalSurahWords,
          livePhonemes: liveStream ?? state.livePhonemes,
          realText: data['real_text']?.toString() ?? state.realText,
          expected: data['expected']?.toString() ?? state.expected,
          predicted: data['predicted']?.toString() ?? state.predicted,
          accuracy: (data['accuracy'] as num?)?.toDouble() ?? state.accuracy,
          per: (data['per'] as num?)?.toDouble() ?? state.per,
          diff: data.containsKey('diff') ? _parseDiff(data['diff']) : state.diff,
          mistakes: (data['mistakes'] as List?)?.map((e) => e.toString()).toList() ?? state.mistakes,
          // Finalized: nothing left to spotlight.
          activeWordIndex: null,
        );
      } else if (isOpenMic) {
        // Live per-ayah highlight as the server detects/progresses.
        final int? currentAyah = detectedAyah ?? state.openMicAyah;
        final mergedSurahWords = Map<int, List<WordTrackResult>>.from(state.surahWords);

        if (state.words.isEmpty && serverWords.isNotEmpty) {
          // First detection seeds the ayah wholesale (border + words light up).
          if (currentAyah != null) mergedSurahWords[currentAyah] = serverWords;
          state = state.copyWith(
            words: serverWords,
            openMicAyah: detectedAyah,
            surahWords: mergedSurahWords,
            livePhonemes: liveStream ?? state.livePhonemes,
            activeWordIndex: (data['active_index'] as num?)?.toInt() ?? 0,
          );
          return;
        }

        // The server re-detected a LATER ayah (forward advance): replace the
        // whole word state with the new ayah and drop stale metrics so nothing
        // from the previous ayah leaks into the new one.
        if (detectedAyah != null &&
            state.openMicAyah != null &&
            detectedAyah != state.openMicAyah &&
            serverWords.isNotEmpty) {
          print("🎯 [OPEN MIC] Advanced to ayah $detectedAyah — moving highlight");
          if (currentAyah != null) mergedSurahWords[currentAyah] = serverWords;
          state = state.copyWith(
            words: serverWords,
            openMicAyah: detectedAyah,
            surahWords: mergedSurahWords,
            livePhonemes: liveStream ?? state.livePhonemes,
            realText: '',
            expected: '',
            predicted: '',
            accuracy: 0,
            per: 0,
            diff: [],
            mistakes: [],
            activeWordIndex: (data['active_index'] as num?)?.toInt() ?? 0,
          );
          return;
        }

        // OR-merge letter/word highlights into the current ayah.
        final merged = _mergeServerWords(state.words, serverWords);
        if (currentAyah != null) mergedSurahWords[currentAyah] = merged;
        state = state.copyWith(
          words: merged,
          surahWords: mergedSurahWords,
          livePhonemes: liveStream ?? state.livePhonemes,
          activeWordIndex: (data['active_index'] as num?)?.toInt() ?? state.activeWordIndex,
        );
      } else {
        // Single-ayah interim: OR-merge words/letters only.
        state = state.copyWith(
          words: _mergeServerWords(state.words, serverWords),
          activeWordIndex: (data['active_index'] as num?)?.toInt() ?? state.activeWordIndex,
        );
      }
    }
  }

  // Layer server-confidence (word + letter) marks onto the local ayah words.
  // Index alignment keeps word order stable; server words are authoritative for
  // per-letter status and OR-accumulate the "read" flag across heartbeats.
  List<WordTrackResult> _mergeServerWords(
      List<WordTrackResult> localWords, List<WordTrackResult> serverWords) {
    if (serverWords.isEmpty) return localWords;
    final normalizedBackend =
        serverWords.where((w) => w.isRead).map((w) => _normalizeArabic(w.text)).toList();

    return localWords.asMap().entries.map((entry) {
      final index = entry.key;
      final localWord = entry.value;
      final normalizedLocal = _normalizeArabic(localWord.text);
      bool isWordRead =
          localWord.isRead || normalizedBackend.contains(normalizedLocal);

      // Carry per-letter highlights from the server (align by index; server
      // keeps the same word order as the local ayah words).
      List<LetterHit> letters = localWord.letters;
      if (index < serverWords.length) {
        final sw = serverWords[index];
        isWordRead = isWordRead || sw.isRead;
        if (sw.letters.isNotEmpty) letters = sw.letters;
      }
      return WordTrackResult(text: localWord.text, isRead: isWordRead, letters: letters);
    }).toList();
  }

  // Surah mode: tell the server the current ayah is finished.
  Future<void> nextAyah() async {
    print("🔵 [RECITATION PROV] Sending 'next' to advance the surah...");
    try {
      await ref.read(repositoryProvider).nextAyah();
    } catch (e) {
      print("❌ [NEXT ERROR] $e");
    }
  }

  Future<void> stopReciting() async {
    print("🔵 [RECITATION PROV] Requesting authoritative final from server...");
    try {
      // Sending "stop" keeps the channel open so the {final:true} message can be
      // delivered; onDone fires afterwards and sets the state to idle.
      await ref.read(repositoryProvider).stopStreaming();
    } catch (e) {
      print("❌ [STOP ERROR] $e");
    }
    // Fallback: if the server never replies, force-close and go idle.
    Future.delayed(const Duration(seconds: 15), () async {
      try {
        await ref.read(dataSourceProvider).forceClose();
      } catch (_) {}
      if (state.status == RecitationStatus.recording) {
        state = state.copyWith(
          status: RecitationStatus.idle,
          activeWordIndex: null,
        );
      }
    });
  }
}

final recitationProvider = NotifierProvider<RecitationNotifier, RecitationResult>(RecitationNotifier.new);