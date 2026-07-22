// debate-web-demo — disposable dev tool. See the comment in index.html.
// Vanilla JS, no build step; livekit-client (the real BROWSER SDK, not the
// react-native one) is imported straight from a CDN.

const $ = (id) => document.getElementById(id);
const setStatus = (text) => { $('status').textContent = text; };

// ---------------------------------------------------------------- livekit sdk
// Dynamic import so a CDN failure produces a readable message instead of a
// silently dead page. Version pinned to match what the backend was tested with.
let Room, RoomEvent, createLocalTracks;
try {
  ({ Room, RoomEvent, createLocalTracks } =
    await import('https://esm.sh/livekit-client@2.20.1?bundle'));
} catch (err) {
  setStatus(`Failed to load livekit-client from esm.sh — are you online? (${err})`);
  throw err;
}

// ------------------------------------------------------------------- config
const API_BASE_KEY = 'debate_web_api_base';
const DEVICE_ID_KEY = 'debate_web_device_id';
const FACT_CHECK_TOPIC = 'fact_check';
const AGENT_IDENTITY = 'fc-agent';

const apiBaseInput = $('apiBase');
apiBaseInput.value = localStorage.getItem(API_BASE_KEY) || 'http://localhost:8000';
apiBaseInput.addEventListener('change', () => {
  localStorage.setItem(API_BASE_KEY, apiBaseInput.value.trim());
  location.reload(); // throwaway tool: a reload is the simplest re-init
});

const apiBase = () => (localStorage.getItem(API_BASE_KEY) || 'http://localhost:8000')
  .trim().replace(/\/+$/, '');
const wsBase = () => {
  const base = apiBase();
  if (base.startsWith('https://')) return 'wss://' + base.slice('https://'.length);
  if (base.startsWith('http://')) return 'ws://' + base.slice('http://'.length);
  return base;
};

// --------------------------------------------------------------------- state
let jwt = null;
let matchWs = null;
let match = null;         // the match_found payload
let room = null;
let localTracks = [];
let leaving = false;      // we initiated the disconnect
let micMuted = false;

// ---------------------------------------------------------------------- auth
async function authenticate() {
  let deviceId = localStorage.getItem(DEVICE_ID_KEY);
  if (!deviceId) {
    deviceId = crypto.randomUUID();
    localStorage.setItem(DEVICE_ID_KEY, deviceId);
  }
  const res = await fetch(`${apiBase()}/auth/device`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ device_id: deviceId, over_18: true }),
  });
  if (!res.ok) throw new Error(`auth failed: ${res.status}`);
  const data = await res.json();
  jwt = data.token;
  $('authState').textContent = `user: ${data.user_id.slice(0, 8)}…`;
  $('authState').classList.add('active');
}

async function loadTopics() {
  const res = await fetch(`${apiBase()}/topics`, {
    headers: { Authorization: `Bearer ${jwt}` },
  });
  if (!res.ok) throw new Error(`topics failed: ${res.status}`);
  const topics = await res.json();
  const select = $('topicSelect');
  select.replaceChildren(...topics.map((t) => {
    const option = document.createElement('option');
    option.value = String(t.id);
    option.textContent = t.title;
    return option;
  }));
}

// --------------------------------------------------------------- matchmaking
function findMatch() {
  const stance = document.querySelector('input[name="stance"]:checked').value;
  const mode = document.querySelector('input[name="mode"]:checked').value;
  const topicId = Number($('topicSelect').value);

  $('picker').hidden = true;
  $('searching').hidden = false;
  setStatus('');

  matchWs = new WebSocket(`${wsBase()}/ws/match?token=${encodeURIComponent(jwt)}`);
  matchWs.onopen = () => {
    matchWs.send(JSON.stringify({
      type: 'join', topic_id: topicId, stance, fact_check_mode: mode,
    }));
  };
  matchWs.onmessage = (event) => {
    let msg;
    try { msg = JSON.parse(event.data); } catch { return; }
    if (msg.type === 'queued') {
      setStatus('Queued — waiting for someone on the other side…');
    } else if (msg.type === 'match_found') {
      match = msg;
      matchWs.onclose = null;
      matchWs.close();
      matchWs = null;
      enterRoom().catch((err) => {
        setStatus(`Failed to join the room: ${err}`);
        resetToPicker();
      });
    } else if (msg.type === 'error') {
      setStatus(`Matchmaking error: ${msg.message}`);
    }
  };
  matchWs.onclose = () => {
    // Only reached while still searching (cleared before entering the room).
    setStatus('Matchmaking connection closed.');
    $('searching').hidden = true;
    $('picker').hidden = false;
  };
}

function cancelSearch() {
  if (matchWs) {
    matchWs.onclose = null;
    matchWs.close(); // server dequeues on disconnect
    matchWs = null;
  }
  $('searching').hidden = true;
  $('picker').hidden = false;
  setStatus('');
}

// ---------------------------------------------------------------------- room
async function enterRoom() {
  $('searching').hidden = true;
  $('room').hidden = false;
  $('roomTopic').textContent = match.topic.title;
  $('roomStances').textContent =
    `You: ${match.your_stance.toUpperCase()} vs Them: ${match.peer_stance.toUpperCase()}`;
  $('notice').textContent = '';
  leaving = false;

  // Camera/mic first so the permission prompt fails fast.
  localTracks = await createLocalTracks({ audio: true, video: true });

  room = new Room();

  room.on(RoomEvent.TrackSubscribed, (track, _pub, participant) => {
    if (participant.identity === AGENT_IDENTITY) return; // hidden fact-checker
    if (track.kind === 'video') {
      track.attach($('remoteVideo'));
    } else if (track.kind === 'audio') {
      const el = track.attach(); // hidden <audio>
      el.dataset.demoRemoteAudio = '1';
      document.body.appendChild(el);
    }
  });

  room.on(RoomEvent.TrackUnsubscribed, (track) => {
    track.detach().forEach((el) => { if (el.tagName === 'AUDIO') el.remove(); });
  });

  room.on(RoomEvent.ParticipantDisconnected, (participant) => {
    if (participant.identity !== AGENT_IDENTITY) {
      $('notice').textContent = 'Opponent left.';
    }
  });

  room.on(RoomEvent.DataReceived, (payload, _participant, _kind, topic) => {
    // The agent is a hidden participant: its packets arrive with no sender,
    // so filter on topic + message shape only.
    if (topic !== FACT_CHECK_TOPIC) return;
    let msg;
    try { msg = JSON.parse(new TextDecoder().decode(payload)); } catch { return; }
    if (msg && typeof msg === 'object') onAgentMessage(msg);
  });

  room.on(RoomEvent.AudioPlaybackStatusChanged, () => {
    // Browser autoplay policy can block remote audio until a user gesture.
    $('audioUnlockBtn').hidden = room.canPlaybackAudio;
  });

  room.on(RoomEvent.Disconnected, () => {
    if (leaving) return;
    setStatus('Room disconnected.');
    teardownRoom();
  });

  await room.connect(match.livekit_url, match.livekit_token);
  for (const track of localTracks) {
    await room.localParticipant.publishTrack(track);
    if (track.kind === 'video') track.attach($('localVideo'));
  }
}

async function endMatch() {
  leaving = true;
  try {
    await fetch(`${apiBase()}/matches/${match.match_id}/end`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${jwt}` },
    });
  } catch { /* throwaway tool: ending is best-effort */ }
  teardownRoom();
}

function teardownRoom() {
  if (room) { leaving = true; room.disconnect(); room = null; }
  for (const track of localTracks) { try { track.stop(); } catch { /* ignore */ } }
  localTracks = [];
  document.querySelectorAll('[data-demo-remote-audio]').forEach((el) => el.remove());
  $('remoteVideo').srcObject = null;
  $('localVideo').srcObject = null;
  $('feed').replaceChildren();
  agentReady(false);
  micMuted = false;
  $('muteBtn').textContent = 'Mute mic';
  resetToPicker();
}

function resetToPicker() {
  match = null;
  $('room').hidden = true;
  $('searching').hidden = true;
  $('picker').hidden = false;
}

// ---------------------------------------------------------------- fact check
let factCheckTimer = null;

function agentReady(ready) {
  const chip = $('agentChip');
  chip.textContent = ready ? 'fact-checker: active' : 'fact-checker: waiting…';
  chip.classList.toggle('active', ready);
  $('factCheckBtn').disabled = !ready;
}

function onAgentMessage(msg) {
  switch (msg.type) {
    case 'agent_ready': // may arrive more than once; idempotent
      agentReady(true);
      break;
    case 'fact_check_status':
      feedNotice(`checking ${msg.target_stance}'s last 30s…`);
      break;
    case 'verdict':
      renderVerdict(msg);
      reenableFactCheck();
      break;
    case 'fact_check_error':
      feedNotice(`fact-check error: ${msg.message}`);
      reenableFactCheck();
      break;
    default:
      break; // unknown types ignored
  }
}

function requestFactCheck() {
  const btn = $('factCheckBtn');
  btn.disabled = true;
  // Re-enable on verdict/error, or after 20s if the agent went silent.
  factCheckTimer = setTimeout(reenableFactCheck, 20_000);
  room.localParticipant.publishData(
    new TextEncoder().encode(JSON.stringify({ type: 'fact_check_request' })),
    { reliable: true, topic: FACT_CHECK_TOPIC },
  );
}

function reenableFactCheck() {
  clearTimeout(factCheckTimer);
  if (room) $('factCheckBtn').disabled = false;
}

function feedNotice(text) {
  const div = document.createElement('div');
  div.className = 'feed-notice';
  div.textContent = `${new Date().toLocaleTimeString()} — ${text}`;
  $('feed').prepend(div);
}

function renderVerdict(v) {
  const card = document.createElement('div');
  card.className = 'verdict-card';

  const head = document.createElement('div');
  const word = document.createElement('span');
  word.className = `verdict-word v-${v.verdict}`;
  word.textContent = v.verdict;
  head.append(word, ` (${v.confidence} confidence, ${v.mode}, speaker: ${v.speaker_stance})`);

  const claim = document.createElement('div');
  claim.className = 'claim';
  claim.textContent = `“${v.claim}”`;

  const summary = document.createElement('div');
  summary.textContent = v.summary;

  card.append(head, claim, summary);

  if (Array.isArray(v.sources) && v.sources.length) {
    const sources = document.createElement('div');
    sources.className = 'sources';
    for (const s of v.sources) {
      if (!s || typeof s.url !== 'string' || !/^https?:\/\//.test(s.url)) continue;
      const a = document.createElement('a');
      a.href = s.url;
      a.target = '_blank';
      a.rel = 'noreferrer noopener';
      a.textContent = s.title || s.url;
      sources.appendChild(a);
    }
    card.appendChild(sources);
  }
  $('feed').prepend(card);
}

// ---------------------------------------------------------------------- init
$('findBtn').addEventListener('click', findMatch);
$('cancelBtn').addEventListener('click', cancelSearch);
$('endBtn').addEventListener('click', endMatch);
$('factCheckBtn').addEventListener('click', requestFactCheck);
$('audioUnlockBtn').addEventListener('click', () => room?.startAudio());
$('muteBtn').addEventListener('click', async () => {
  micMuted = !micMuted;
  const audio = localTracks.find((t) => t.kind === 'audio');
  if (audio) { micMuted ? await audio.mute() : await audio.unmute(); }
  $('muteBtn').textContent = micMuted ? 'Unmute mic' : 'Mute mic';
});

try {
  setStatus('Authenticating…');
  await authenticate();
  await loadTopics();
  setStatus('');
  $('picker').hidden = false;
} catch (err) {
  setStatus(`Cannot reach the API at ${apiBase()} — is debate-api running? (${err.message})`);
  $('authState').textContent = 'auth: failed';
}
