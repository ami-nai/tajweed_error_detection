import 'dart:io' show Platform;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../providers/recitation_provider.dart';
import '../../domain/entities/recitation_result.dart';

/// Thesis measurement screen (separate from the recitation flow).
///
/// Shows the on-device round-trip readout: per-beat mic-send -> interim-recv
/// latency measured with the phone's own clock (no USB/logcat needed).
/// Read-only: it never starts/stops recording and never mutates state.
class MeasurementPage extends ConsumerWidget {
  const MeasurementPage({super.key});

  String _modeLabel(RecitationMode mode) {
    return switch (mode) {
      RecitationMode.singleAyah => 'Single ayah',
      RecitationMode.surah => 'Full surah',
      RecitationMode.openMic => 'Open mic',
    };
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final state = ref.watch(recitationProvider);
    final hasData = state.rttBeats > 0;
    final recent = state.rttSamples.length <= 30
        ? state.rttSamples
        : state.rttSamples.sublist(state.rttSamples.length - 30);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Edge Measurements'),
        centerTitle: true,
      ),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(20.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Card(
              elevation: 2,
              shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
              child: Padding(
                padding: const EdgeInsets.all(16.0),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text('Device & session',
                        style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
                    const SizedBox(height: 8),
                    Text('Device: ${Platform.isAndroid ? "Android" : Platform.operatingSystem}'),
                    Text('Backend: $kBackendUrl'),
                    Text('Mode: ${_modeLabel(state.mode)}'
                        ' · Surah ${state.selectedSurah}'
                        ' · Ayah ${state.selectedAyah}'),
                    Text('Status: ${state.status.name}'),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 12),
            Card(
              elevation: 2,
              shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
              child: Padding(
                padding: const EdgeInsets.all(16.0),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    const Text('Round-trip per beat (ms)',
                        style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
                    const SizedBox(height: 4),
                    const Text(
                      'Mic chunk send -> interim received, same-device clock. '
                      'Includes 0.5 s tick quantization + decode + network + render.',
                      style: TextStyle(fontSize: 12, color: Colors.black54),
                    ),
                    const SizedBox(height: 12),
                    if (!hasData)
                      const Text('No beats yet — recite to measure.',
                          style: TextStyle(fontSize: 15, color: Colors.black54))
                    else ...[
                      Center(
                        child: Text('${state.rttMedianMs} ms',
                            style: const TextStyle(
                                fontSize: 44, fontWeight: FontWeight.bold, color: Colors.teal)),
                      ),
                      const Center(
                        child: Text('median',
                            style: TextStyle(fontSize: 13, color: Colors.black54)),
                      ),
                      const SizedBox(height: 12),
                      _statRow('Last', '${state.rttLastMs} ms'),
                      _statRow('Min', '${state.rttMinMs} ms'),
                      _statRow('Max', '${state.rttMaxMs} ms'),
                      _statRow('Beats', '${state.rttBeats}'),
                    ],
                  ],
                ),
              ),
            ),
            const SizedBox(height: 12),
            if (hasData)
              Card(
                elevation: 2,
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                child: Padding(
                  padding: const EdgeInsets.all(16.0),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text('Recent beats (newest first)',
                          style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
                      const SizedBox(height: 8),
                      Text(
                        recent.reversed.map((v) => '$v').join(' · '),
                        style: const TextStyle(fontSize: 14),
                      ),
                    ],
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }

  Widget _statRow(String label, String value) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3.0),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Text(label, style: const TextStyle(fontSize: 14, color: Colors.black54)),
          Text(value, style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600)),
        ],
      ),
    );
  }
}
