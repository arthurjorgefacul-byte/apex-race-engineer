"""PTT lifetime tests with no microphone or Windows process invocation."""
import io
import json
import pathlib
import re
import unittest
from unittest import mock
from engineer.recognition import PushToTalk


class Process:
    def __init__(self, lines=''):
        self.stdout=io.StringIO(lines); self.ended=False; self.terminated=0
    def poll(self): return 0 if self.ended else None
    def terminate(self): self.terminated+=1; self.ended=True
    def kill(self): self.ended=True
    def wait(self, timeout=None): return 0


class RecognitionTests(unittest.TestCase):
    def test_construction_and_close_never_open_microphone(self):
        with mock.patch('engineer.recognition.subprocess.Popen') as start:
            ptt=PushToTalk(lambda topic:None)
            self.assertFalse(ptt.snapshot()['listening'])
            ptt.close(); start.assert_not_called()
            with self.assertRaises(ValueError): ptt.start()

    def test_release_ends_only_owned_worker_even_if_noisy_recognizer_never_finishes(self):
        ptt=PushToTalk(lambda topic:None); process=Process(); ptt.process=process
        ptt.requested=ptt.listening=True; ptt.stop()
        self.assertEqual(process.terminated,1); self.assertIsNone(ptt.process)
        self.assertFalse(ptt.snapshot()['listening']); self.assertFalse(ptt.snapshot()['starting'])

    def test_recognized_command_stops_input_before_answer(self):
        answers=[];ptt=PushToTalk(lambda topic:answers.append((topic,ptt.snapshot()['listening'])))
        process=Process('{"event":"recognized","text":"fuel status","topic":"fuel"}\n')
        ptt.process=process;ptt.listening=ptt.requested=True;ptt._read(process)
        self.assertEqual(answers,[('fuel',False)]); self.assertEqual(process.terminated,1)

    def test_new_race_coach_and_compound_commands_reach_the_engineer_after_input_closes(self):
        for topic in ('personal_best', 'race_best', 'others_best', 'gap_ahead', 'gap_behind',
                      'gap_leader', 'change_compound', 'feedback', 'compound_soft', 'compound_medium',
                      'compound_hard', 'compound_intermediate', 'compound_wet'):
            with self.subTest(topic=topic):
                answers=[]
                ptt=PushToTalk(lambda value:answers.append((value, ptt.process, ptt.snapshot()['listening'])))
                process=Process(json.dumps({'event':'recognized','text':topic,'topic':topic})+'\n')
                ptt.process=process; ptt.requested=ptt.listening=True; ptt._read(process)
                self.assertEqual(answers, [(topic, None, False)])
                self.assertEqual(process.terminated, 1)

    def test_windows_grammar_and_python_command_boundary_agree(self):
        # A worker can recognize successfully but still be silently discarded
        # unless its topic is also accepted across the Python process boundary.
        worker=pathlib.Path(__file__).parents[1]/'engineer'/'recognition_worker.ps1'
        grammar=worker.read_text(encoding='utf-8').split('private static string Escape', 1)[0]
        pairs=re.findall(r'\{"([^"\n]+)","([^"\n]+)"\}', grammar)
        self.assertGreater(len(pairs), 70)
        self.assertEqual(len({phrase for phrase, _ in pairs}), len(pairs), 'C# dictionary rejects duplicate phrases')
        self.assertEqual({topic for _, topic in pairs}, PushToTalk.TOPICS)

    def test_unknown_worker_topic_is_not_a_command(self):
        answers=[];ptt=PushToTalk(answers.append)
        process=Process('bad json\n{"event":"recognized","text":"x","topic":"not_a_command"}\n')
        ptt.process=process;ptt._read(process);self.assertEqual(answers,[])

    def test_old_reader_cannot_stop_replacement_worker(self):
        ptt=PushToTalk(lambda topic:None);old=Process();current=Process();ptt.process=current
        ptt.requested=ptt.listening=True;ptt._read(old)
        self.assertIs(ptt.process,current);self.assertTrue(ptt.snapshot()['listening'])

    def test_device_error_is_visible_and_input_state_resets(self):
        ptt=PushToTalk(lambda topic:None);process=Process('{"event":"error","message":"No input device"}\n')
        ptt.process=process;ptt.requested=True;ptt._read(process)
        self.assertEqual(ptt.snapshot()['error'],'No input device');self.assertFalse(ptt.snapshot()['starting'])


if __name__=='__main__':unittest.main()
