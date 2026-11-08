import hashlib
from io import BytesIO
from types import SimpleNamespace
import unittest

from fastapi import HTTPException
from eva_asr.app import transcribe
from eva_asr.worker import phrases, restore_opening


def segment(start,end,text):
    return dict(speaker="unknown",start_seconds=start,end_seconds=end,text=text)


class SpeechTests(unittest.TestCase):
    def test_start_recovery_requires_shared_words_at_overlapping_times(self):
        original=[segment(8,13,"Откройте главный экран приложения.")]
        hello=segment(3,4,"Здравствуйте!")
        overlap=segment(9,12,"Главный экран приложения.")
        self.assertEqual(restore_opening(original,[hello,overlap]),[hello,*original])
        self.assertEqual(restore_opening(original,[hello,segment(9,12,"Другой текст.")]),original)
        self.assertEqual(restore_opening(original,[hello,segment(13,15,overlap["text"])]),original)
        self.assertEqual(restore_opening(original,[overlap]),original)

    def test_phrases_keep_word_times_and_require_review_of_roles(self):
        word=lambda text,a,b:SimpleNamespace(word=text,start=a,end=b)
        raw=SimpleNamespace(words=[word("Привет.",1,1.5),word(" Да",2.4,2.9),word(" верно.",3,3.7)])
        self.assertEqual(phrases([raw],3.6),[segment(1,1.5,"Привет."),segment(2.4,3.6,"Да верно.")])

    def test_mismatched_checksum_never_launches_recognizer(self):
        file=SimpleNamespace(content_type="audio/mpeg",file=BytesIO(b"synthetic"))
        with self.assertRaises(HTTPException) as caught:
            transcribe(file,"RU",hashlib.sha256(b"other bytes").hexdigest())
        self.assertEqual(caught.exception.status_code,422)
