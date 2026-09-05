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

  void setMode(RecitationMode mode) {
    if (mode == state.mode) return;
    state = state.copyWith(mode: mode, status: RecitationStatus.idle);
    if (mode == RecitationMode.singleAyah) {
      _loadSingleAyah(state.selectedSurah, state.selectedAyah);
    } else {
      _loadSurah(state.selectedSurah);
    }
  }

  void updateSelection(int surahId, int ayahId) {
    if (state.mode == RecitationMode.singleAyah) {
      _loadSingleAyah(surahId, ayahId);
    } else {
      _loadSurah(surahId);
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
      ayahWER: {},
      surahWER: 0,
      diff: [],
    );
  }

  Future<void> startReciting({required int surahId, int? ayahId}) async {
    try {
      await _serverSubscription?.cancel();

      if (state.mode == RecitationMode.singleAyah && ayahId != null) {
        final staticWords = quranTextDatabase[surahId]?[ayahId] ?? [];
        state = state.copyWith(
          status: RecitationStatus.recording,
          words: staticWords.map((w) => WordTrackResult(text: w, isRead: false)).toList(),
        );
      } else {
        // Surah mode
        _loadSurah(surahId);
        state = state.copyWith(status: RecitationStatus.recording);
      }

      print("🔵 [RECITATION PROV] Preparing stream for Surah: $surahId, mode: ${state.mode}");
      final repository = ref.read(repositoryProvider);
      final serverStream = await repository.startStreaming(surahId, ayahId);
      print("🟢 [RECITATION PROV] Stream connection successfully initiated!");

      _serverSubscription = serverStream.listen(
        (event) {
          print("📥 [SERVER EVENT RECEIVED]: $event");
          try {
            final Map<String, dynamic> data = jsonDecode(event);
            _handleServerPayload(data);
          } catch (parseError) {
            print("❌ [PARSER ERROR] Error interpreting JSON payload: $parseError");
          }
        },
        onError: (error) {
          print("❌ [STREAM ERROR] The server stream emitted an error: $error");
          state = state.copyWith(status: RecitationStatus.error);
          stopReciting();
        },
        onDone: () {
          print("🔌 [STREAM CLOSED] The server connection closed down automatically (onDone).");
        }
      );
    } catch (e) {
      print("❌ [CRITICAL FAILURE] Failed during startReciting initiation sequence: $e");
      state = state.copyWith(status: RecitationStatus.error);
    }
  }

  void _handleServerPayload(Map<String, dynamic> data) {
    final mode = data['mode']?.toString() ?? 'single';

    if (mode == 'surah') {
      // Fast "advance" message: pause detected, tell the user which ayah to read next
      // immediately, without waiting for the inference result of the finished ayah.
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

      // Sequential surah mode
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

      // Accuracies
      final ayahAcc = <int, double>{};
      final accData = data['ayah_accuracies'];
      if (accData is Map) {
        accData.forEach((k, v) {
          if (v != null) ayahAcc[int.parse(k.toString())] = (v as num).toDouble();
        });
      }

      // WER per ayah
      final ayahWER = <int, double>{};
      final werData = data['ayah_wers'];
      if (werData is Map) {
        werData.forEach((k, v) {
          if (v != null) ayahWER[int.parse(k.toString())] = (v as num).toDouble();
        });
      }

      final isFinished = data['finished'] == true;
      final nextAyah = (data['next_ayah'] as num?)?.toInt() ?? 0;
      state = state.copyWith(
        status: isFinished ? RecitationStatus.success : RecitationStatus.recording,
        words: [],
        surahWords: updated,
        ayahAccuracies: ayahAcc,
        surahAverage: (data['surah_average'] as num?)?.toDouble() ?? state.surahAverage,
        currentAyah: finalAyahId,
        nextAyah: nextAyah,
        ayahWER: ayahWER,
        surahWER: (data['surah_wer'] as num?)?.toDouble() ?? state.surahWER,
        diff: _parseDiff(data['diff']),
      );
      print("📈 [SURAH STATE] avg=${state.surahAverage} current=${state.currentAyah}");
    } else {
      // Single ayah mode (original logic)
      List<String> recognizedWords = [];
      List<dynamic> serverWords = [];
      if (data.containsKey('words')) {
        final words = data['words'];
        if (words is List) {
          serverWords = words;
          for (var item in words) {
            if (item is Map) {
              if (item['is_read'] == true || item['isRead'] == true) {
                recognizedWords.add(item['text']?.toString() ?? '');
              }
            } else if (item is String) {
              recognizedWords.add(item);
            }
          }
        }
      }

      final normalizedBackend = recognizedWords.map((w) => _normalizeArabic(w)).toList();

      final mergedWords = state.words.asMap().entries.map((entry) {
        final index = entry.key;
        final localWord = entry.value;
        final normalizedLocal = _normalizeArabic(localWord.text);
        bool isWordRead = localWord.isRead || normalizedBackend.contains(normalizedLocal);

        // Carry per-letter highlights from the server (align by index; server
        // keeps the same word order as the local ayah words).
        List<LetterHit> letters = localWord.letters;
        if (index < serverWords.length && serverWords[index] is Map) {
          final sw = (serverWords[index] as Map).cast<String, dynamic>();
          final sRead = sw['is_read'] == true || sw['isRead'] == true;
          isWordRead = isWordRead || sRead;
          if (sw['letters'] is List) {
            letters = (sw['letters'] as List).map((l) {
              if (l is Map) {
                return LetterHit.fromJson(l.cast<String, dynamic>());
              }
              return LetterHit(ch: l.toString(), status: LetterStatus.neutral);
            }).toList();
          }
        }

        return WordTrackResult(text: localWord.text, isRead: isWordRead, letters: letters);
      }).toList();

      state = state.copyWith(
        words: mergedWords,
        realText: data['real_text']?.toString() ?? state.realText,
        expected: data['expected']?.toString() ?? state.expected,
        predicted: data['predicted']?.toString() ?? state.predicted,
        accuracy: (data['accuracy'] as num?)?.toDouble() ?? state.accuracy,
        wer: (data['wer'] as num?)?.toDouble() ?? state.wer,
        diff: _parseDiff(data['diff']),
      );
    }
  }

  Future<void> stopReciting() async {
    print("🔵 [RECITATION PROV] Manually terminating stream...");
    await _serverSubscription?.cancel();
    await ref.read(repositoryProvider).stopStreaming();
    state = state.copyWith(status: RecitationStatus.idle);
  }
}

final recitationProvider = NotifierProvider<RecitationNotifier, RecitationResult>(RecitationNotifier.new);
