"""Offline API and audio checks; no cloud credentials or calls required."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import soundfile as sf

import server2 as service


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.client = service.app.test_client()
        self.original_voice = service.TTS_VOICE

    def tearDown(self):
        service.TTS_VOICE = self.original_voice

    def test_control_page_and_health_without_credentials(self):
        with patch.object(service, 'DASHSCOPE_API_KEY', ''):
            page = self.client.get('/')
            self.assertEqual(page.status_code, 200)
            self.assertIn('数字人音色控制台', page.get_data(as_text=True))
            self.assertEqual(self.client.get('/health').status_code, 200)
            voices = self.client.get('/voices').get_json()
            self.assertEqual(len(voices['voices']), 22)
            self.assertIn(voices['current_voice'], service.VOICE_IDS)

    def test_voice_selection_and_invalid_voice(self):
        response = self.client.post('/set_voice', json={'voice': 'Cherry'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get('/voices').get_json()['current_voice'], 'Cherry')
        self.assertEqual(self.client.post('/set_voice', json={'voice': 'invalid'}).status_code, 400)
        self.assertEqual(service.TTS_VOICE, 'Cherry')

    def test_required_fields(self):
        for endpoint in ('/generate_voice', '/recognize_speech'):
            with self.subTest(endpoint=endpoint):
                self.assertEqual(self.client.post(endpoint, json={}).status_code, 400)

    def test_missing_key_does_not_call_cloud(self):
        with patch.object(service, 'DASHSCOPE_API_KEY', ''), patch.object(
            service.dashscope.MultiModalConversation, 'call'
        ) as cloud_call, tempfile.TemporaryDirectory() as tmp:
            response = self.client.post('/generate_voice', json={'text': '你好'})
            self.assertEqual(response.status_code, 503)
            cloud_call.assert_not_called()
            audio = Path(tmp) / 'input.wav'
            sf.write(audio, np.zeros(1600), 16000, subtype='PCM_16')
            response = self.client.post('/recognize_speech', json={'audio_path': str(audio)})
            self.assertEqual(response.status_code, 503)

    def test_stereo_audio_conversion_for_ue5_and_asr(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'source.wav'
            t = np.arange(2400) / 24000
            wave = np.sin(2 * np.pi * 440 * t) * 0.25
            sf.write(source, np.column_stack((wave, wave)), 24000, subtype='FLOAT')
            for converter, rate in ((service.convert_audio_for_ue5, 48000),
                                    (service.convert_audio_for_asr, 16000)):
                with self.subTest(rate=rate):
                    target = Path(tmp) / f'{rate}.wav'
                    converter(str(source), str(target))
                    info = sf.info(target)
                    self.assertEqual((info.samplerate, info.channels, info.subtype), (rate, 1, 'PCM_16'))
                    self.assertAlmostEqual(info.duration, 0.1, places=3)

    def test_ready_audio_is_copied_without_resampling(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp) / 'source.wav', Path(tmp) / 'target.wav'
            sf.write(source, np.zeros(4800), 48000, subtype='PCM_16')
            result = service.convert_audio_for_ue5(str(source), str(target))
            self.assertFalse(result['converted'])
            self.assertEqual(source.read_bytes(), target.read_bytes())

    def test_tts_download_conversion_and_audio_route(self):
        def fake_download(url, output):
            self.assertEqual(url, 'https://example.invalid/audio.wav')
            sf.write(output, np.zeros(2400), 24000, subtype='PCM_16')

        response = SimpleNamespace(status_code=200, output={'audio': {'url': 'https://example.invalid/audio.wav'}})
        with tempfile.TemporaryDirectory() as tmp, patch.object(service, 'TTS_OUTPUT_DIR', tmp), \
                patch.object(service, 'DASHSCOPE_API_KEY', 'test-placeholder'), \
                patch.object(service.dashscope.MultiModalConversation, 'call', return_value=response) as call, \
                patch.object(service, 'download_audio', side_effect=fake_download):
            service.TTS_VOICE = 'Cherry'
            result = self.client.post('/generate_voice', json={'text': '你好'})
            self.assertEqual(result.status_code, 200)
            data = result.get_json()
            self.assertEqual(data['voice'], 'Cherry')
            self.assertEqual(call.call_args.kwargs['voice'], 'Cherry')
            self.assertEqual(sf.info(data['audio_path']).samplerate, 48000)
            audio_response = self.client.get(data['audio_url'])
            self.assertEqual(audio_response.status_code, 200)
            self.assertTrue(audio_response.data.startswith(b'RIFF'))
            audio_response.close()
            self.assertEqual(len(list(Path(tmp).glob('*.wav'))), 1)


if __name__ == '__main__':
    unittest.main()
