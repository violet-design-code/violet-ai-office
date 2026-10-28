'use strict';
// No remote recognition fallback, language pack install, network fetch or automatic speech.
(() => {
  const start = document.getElementById('voice-start');
  const stop = document.getElementById('voice-stop');
  const read = document.getElementById('voice-read');
  const status = document.getElementById('voice-status');
  const input = document.getElementById('schedule-instruction');
  const answer = document.getElementById('schedule-answer');
  if (!start || !stop || !read || !status || !input || !answer) return;
  let recognition = null;
  const Recognition = window.SpeechRecognition; // Prefixed/network-only APIs deliberately not used.
  async function localReady() {
    if (!window.isSecureContext || !Recognition || !('processLocally' in Recognition.prototype) || typeof Recognition.available !== 'function') return false;
    try { return (await Recognition.available({langs: ['ja-JP'], processLocally: true})) === 'available'; }
    catch (_) { return false; }
  }
  localReady().then(ready => {
    start.disabled = !ready;
    status.textContent = ready ? '端末内の日本語音声認識を利用できます。マイク許可は開始時に確認します。' : '端末内の日本語認識は未対応・未準備です。文字入力をご利用ください。外部認識へは切り替えません。';
  });
  start.addEventListener('click', async () => {
    if (!(await localReady()) || recognition) return;
    recognition = new Recognition();
    recognition.lang = 'ja-JP'; recognition.processLocally = true; recognition.continuous = false; recognition.interimResults = false;
    recognition.onresult = event => {
      input.value = event.results[0][0].transcript;
      status.textContent = '認識した文字を確認し、「指示を確認・検索する」を押してください。まだ登録していません。';
    };
    recognition.onerror = () => { status.textContent = '音声入力を完了できませんでした。文字入力をご利用ください。'; };
    recognition.onend = () => { recognition = null; start.disabled = false; stop.disabled = true; };
    try { recognition.start(); start.disabled = true; stop.disabled = false; }
    catch (_) { recognition = null; start.disabled = false; status.textContent = 'マイクを開始できません。文字入力をご利用ください。'; }
  });
  stop.addEventListener('click', () => { if (recognition) recognition.stop(); });
  const synth = window.speechSynthesis;
  const localVoice = () => synth && synth.getVoices().find(v => v.localService && v.lang.toLowerCase().startsWith('ja'));
  const refresh = () => { read.disabled = !localVoice(); };
  if (synth) { synth.addEventListener('voiceschanged', refresh); refresh(); }
  read.addEventListener('click', () => {
    const voice = localVoice(); if (!voice) return;
    synth.cancel(); const utterance = new SpeechSynthesisUtterance(answer.textContent);
    utterance.lang = 'ja-JP'; utterance.voice = voice; synth.speak(utterance);
  });
  window.addEventListener('pagehide', () => { if (recognition) recognition.abort(); if (synth) synth.cancel(); });
})();
