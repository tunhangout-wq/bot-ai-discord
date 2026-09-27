const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
}[char]));
const number = value => Number(value || 0).toLocaleString('ar');
const date = value => value ? new Date(Number(value) * 1000).toLocaleString('ar', {dateStyle: 'medium', timeStyle: 'short'}) : '—';
const rankNames = {owner: 'المالك', dev: 'ديفيلوبر', founder: 'فاوندر', team: 'فريق'};
const groupNames = {server: 'السيرفر', channel: 'القنوات', member: 'الأعضاء', role: 'الرتب', security: 'الأمان', utility: 'الأدوات', ai: 'الذكاء الاصطناعي', career: 'المهن', loan: 'القروض'};
const commandPath = value => String(value || '').trim().replace(/^\/+/, '').replace(/[\/\s]+/g, '.').split('.').filter(Boolean).join('.');

const API = {
  token: localStorage.getItem('vixen_token'),
  me: null,
  async req(path, options = {}) {
    const headers = {...(options.headers || {}), Authorization: `Bearer ${this.token}`};
    if (options.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 30000);
    let response;
    try {
      response = await fetch(path, {...options, headers, signal: controller.signal});
    } catch (err) {
      if (err.name === 'AbortError') throw Error('انتهت مهلة الانتظار (30 ثانية) — تأكد أن البوت متصل وحاول مجددًا');
      throw Error('تعذر الاتصال بالسيرفر');
    } finally {
      clearTimeout(timer);
    }
    let data = {};
    try { data = await response.json(); } catch (_) {}
    if (response.status === 401) {
      localStorage.removeItem('vixen_token');
      this.token = null;
      $('#app')?.classList.add('hidden');
      $('#login')?.classList.remove('hidden');
    }
    if (!response.ok || data.ok === false) throw Error(data.error || 'تعذر إكمال الطلب');
    return data;
  },
  get(path) { return this.req(path); },
  post(path, body) { return this.req(path, {method: 'POST', body: JSON.stringify(body)}); }
};

let commands = [];
let members = [];
let memberAfter = '';
let memberTimer;
let currentPage = 'overview';
let dmGuilds = [];
let dmMembers = [];
let dmTemplates = [];
let currentDmTemplateId = '';
let dmMemberTimer;
let dmPreviewTimer;
let terminalAbortController = null;

function toast(message, kind = 'success') {
  const node = document.createElement('div');
  node.className = `toast ${kind}`;
  node.innerHTML = `<span>${kind === 'success' ? '✓' : '!'}</span><b>${esc(message)}</b>`;
  $('#toastStack').appendChild(node);
  setTimeout(() => node.remove(), 4200);
}

function setResult(selector, message, kind = 'success') {
  const node = $(selector);
  if (!node) return;
  node.className = `inline-result ${kind}`;
  node.textContent = message;
}

function page(id) {
  const section = document.getElementById(id);
  if (!section || !section.classList.contains('page')) {
    console.error(`[dashboard] page not found: ${id}`);
    toast(`القسم المطلوب غير موجود: ${id}`, 'error');
    return;
  }
  currentPage = id;
  $$('.page').forEach(section => section.classList.toggle('hidden', section.id !== id));
  $$('[data-page]').forEach(button => {
    const active = button.dataset.page === id;
    button.classList.toggle('active', active);
    if (active) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  });
  const titles = {
    overview: 'نظرة عامة', commands: 'مركز الأوامر', members: 'الأعضاء والرسائل',
    security: 'الأمان والإشراف', economy: 'الاقتصاد', staff: 'الفريق والصلاحيات',
    atria: 'AI Moderation', 'ai-chat': 'AI Chat', voice: 'Voice Control', 'dm-center': 'DM Center', 'bot-profile': 'Bot Profile', terminal: 'Live Terminal', logs: 'سجل التدقيق', settings: 'الإعدادات'
  };
  $('#title').textContent = titles[id] || id;
  $('#app').classList.remove('menu-open');
  if (id !== 'terminal') stopTerminal();
  if (id === 'overview') stats();
  if (id === 'commands') renderCommands();
  if (id === 'members') loadMembers(true);
  if (id === 'economy') loadMarket();
  if (id === 'staff') { loadStaff(); loadStaffWarnings(); }
  if (id === 'atria') loadAtria();
  if (id === 'ai-chat') loadAIChat();
  if (id === 'voice') loadVoice();
  if (id === 'dm-center') loadDMCenter();
  if (id === 'bot-profile') loadBotProfile();
  if (id === 'terminal') startTerminal();
  if (id === 'logs') loadLogs();
  if (id === 'settings') loadSettings();
}

function applySession(session) {
  API.me = session;
  $('#operatorName').textContent = session.name || (session.owner ? 'المالك' : 'Dashboard operator');
  $('#operatorRole').textContent = session.rank_name || rankNames[session.rank] || session.rank || 'Staff';
  $('#sessionRank').textContent = session.rank_name || rankNames[session.rank] || 'جلسة الإدارة';
  $('#welcomeName').textContent = session.name || 'operator';
  $('#operatorAvatar').textContent = (session.name || 'V').trim().charAt(0).toUpperCase();
}

async function boot() {
  if (!API.token) return;
  try {
    const session = await API.get('/api/me');
    applySession(session);
    $('#login').classList.add('hidden');
    $('#app').classList.remove('hidden');
    await loadCommands();
    page('overview');
  } catch (_) {
    localStorage.removeItem('vixen_token');
    API.token = null;
  }
}

function setLoginMode(mode) {
  const owner = mode === 'owner';
  $('#ownerTab').classList.toggle('active', owner);
  $('#codeTab').classList.toggle('active', !owner);
  $('#ownerFields').classList.toggle('hidden', !owner);
  $('#codeFields').classList.toggle('hidden', owner);
  $('#loginErr').textContent = '';
}

$('#ownerTab').onclick = () => setLoginMode('owner');
$('#codeTab').onclick = () => setLoginMode('code');
$('#loginForm').onsubmit = async event => {
  event.preventDefault();
  const codeMode = $('#codeFields').classList.contains('hidden') === false;
  const payload = codeMode
    ? {method: 'code', code: $('#staffCode').value.trim()}
    : {method: 'owner', owner_id: $('#owner').value.trim(), password: $('#pass').value};
  try {
    const session = await API.post('/api/login', payload);
    API.token = session.token;
    localStorage.setItem('vixen_token', session.token);
    applySession(session);
    $('#login').classList.add('hidden');
    $('#app').classList.remove('hidden');
    await loadCommands();
    page('overview');
  } catch (error) {
    $('#loginErr').textContent = error.message;
  }
};
$('#logout').onclick = () => {
  localStorage.removeItem('vixen_token');
  API.token = null;
  location.reload();
};
$('#mobileOpen').onclick = () => $('#app').classList.add('menu-open');
$('#mobileClose').onclick = () => $('#app').classList.remove('menu-open');

document.addEventListener('click', event => {
  const pageButton = event.target.closest('[data-page]');
  if (pageButton) {
    event.preventDefault();
    page(pageButton.dataset.page);
    return;
  }
  const commandButton = event.target.closest('[data-command]');
  if (commandButton) {
    event.preventDefault();
    openCommand(commandButton.dataset.command);
    return;
  }
  const dmButton = event.target.closest('[data-dm-member]');
  if (dmButton) {
    $('#dmId').value = dmButton.dataset.dmMember;
    $('#dmText').focus();
  }
  const revokeButton = event.target.closest('[data-revoke-code]');
  if (revokeButton) revokeCode(revokeButton.dataset.revokeCode);
  const removeButton = event.target.closest('[data-remove-staff]');
  if (removeButton) removeStaff(removeButton.dataset.removeStaff);
});

async function stats() {
  try {
    const data = await API.get('/api/stats');
    $('#sUsers').textContent = number(data.users);
    $('#sMoney').textContent = number(data.total_money);
    $('#sLoans').textContent = number(data.active_loans);
    const latency = Number(data.bot?.latency_ms);
    $('#sPing').textContent = Number.isFinite(latency) && latency > 0 ? `${number(latency)}ms` : '—';
    $('#currencyLabel').textContent = `${data.currency_symbol || ''} إجمالي المحفظة والبنك`;
    const connected = Boolean(data.bot?.connected);
    $('#botLabel').textContent = connected ? (data.bot?.name || 'البوت متصل') : 'بانتظار اتصال Discord';
    $('#serverName').textContent = connected ? (data.bot?.name || 'Vixen Discord') : 'وضع الإعداد';
    $('#statusText').textContent = connected ? 'Discord متصل' : 'Dashboard يعمل';
    $('#healthApi').textContent = 'يعمل';
    $('#statusText').previousElementSibling?.classList.toggle('offline', !connected);
    $('#healthGateway').textContent = connected ? 'متصل' : 'بانتظار البوت';
    const gatewayDot = $('#healthGateway').previousElementSibling?.querySelector('.health-dot');
    gatewayDot?.classList.toggle('good', connected);
    gatewayDot?.classList.toggle('offline', !connected);
    const apiDot = $('#healthApi').previousElementSibling?.querySelector('.health-dot');
    apiDot?.classList.add('good');
    apiDot?.classList.remove('offline');
  } catch (error) {
    $('#statusText').textContent = 'تعذر تحديث البيانات';
    $('#healthApi').textContent = 'تعذر الاتصال';
    const apiDot = $('#healthApi').previousElementSibling?.querySelector('.health-dot');
    apiDot?.classList.remove('good');
    apiDot?.classList.add('offline');
  }
}

async function loadCommands() {
  try {
    $('#registryState').textContent = 'جارٍ التحديث…';
    const data = await API.get('/api/commands');
    commands = data.commands || [];
    $('#cmdCount').textContent = commands.length;
    $('#healthCommands').textContent = `${commands.length} أمر`;
    const groups = [...new Set(commands.map(command => command.name.split('.')[0]))].sort();
    $('#commandGroupCount').textContent = groups.length;
    $('#commandRequiredCount').textContent = commands.reduce((total, command) => total + (command.args || []).filter(arg => arg.required).length, 0);
    $('#registryState').textContent = 'متصل';
    $('#cmdGroup').innerHTML = `<option value="">كل المجموعات</option>${groups.map(group => `<option value="${esc(group)}">${esc(groupNames[group] || group)} · ${esc(group)}</option>`).join('')}`;
    renderCommands();
    syncCommandButtons();
  } catch (error) {
    $('#registryState').textContent = 'تعذر الاتصال';
    toast(error.message, 'error');
  }
}

function syncCommandButtons() {
  $$('[data-command]').forEach(button => {
    const wanted = commandPath(button.dataset.command);
    const available = commands.some(command => commandPath(command.name) === wanted);
    button.disabled = !available;
    button.classList.toggle('command-unavailable', !available);
    if (!available) button.title = 'هذا الأمر غير محمّل في البوت الحالي';
  });
}

function renderCommands() {
  const query = ($('#cmdSearch')?.value || '').trim().toLowerCase();
  const group = $('#cmdGroup')?.value || '';
  const sort = $('#cmdSort')?.value || 'name';
  let visible = commands.filter(command => {
    const haystack = `${command.name} ${command.description} ${(command.aliases || []).join(' ')}`.toLowerCase();
    return (!query || haystack.includes(query)) && (!group || command.name.startsWith(`${group}.`) || command.name === group);
  });
  if (sort === 'group') visible.sort((a, b) => a.name.split('.')[0].localeCompare(b.name.split('.')[0]) || a.name.localeCompare(b.name));
  else visible.sort((a, b) => a.name.localeCompare(b.name));
  $('#visibleCommandCount').textContent = visible.length;
  $('#commandsGrid').innerHTML = visible.map(command => {
    const groupName = command.name.split('.')[0];
    const requiredCount = (command.args || []).filter(arg => arg.required).length;
    const args = command.args?.length
      ? command.args.map(arg => `<span class="${arg.required ? 'required' : ''}">${esc(arg.name)}${arg.required ? ' *' : ''}</span>`).join('')
      : '<span>بدون معاملات</span>';
    const aliases = (command.aliases || []).length ? `<small class="aliases">بدائل: ${esc(command.aliases.join('، '))}</small>` : '';
    return `<article class="command-card"><div class="command-meta"><span class="command-group">${esc(groupNames[groupName] || groupName)}</span><span class="command-index">/${esc(command.name)}</span></div><h3>/${esc(command.name.replace('.', ' / '))}</h3><p>${esc(command.description || 'أمر إداري من البوت')}</p><div class="command-card-foot"><span>${requiredCount ? `${requiredCount} معامل مطلوب` : 'جاهز للتنفيذ'}</span><span>${esc(command.cog || 'Vixen')}</span></div><div class="command-args">${args}</div>${aliases}<button class="secondary wide command-run" data-command="${esc(command.name)}">فتح نموذج التنفيذ <span>←</span></button></article>`;
  }).join('') || '<div class="empty-state panel"><span>⌕</span><h3>لا توجد أوامر مطابقة</h3><p>جرّب البحث باسم مختلف أو اختر مجموعة أخرى.</p></div>';
  syncCommandButtons();
}
$('#cmdSearch').oninput = renderCommands;
$('#cmdGroup').onchange = renderCommands;
$('#cmdSort').onchange = renderCommands;
$('#refreshCommands').onclick = loadCommands;

function inputForArg(arg, preset) {
  const value = preset ?? (arg.default ?? '');
  const type = `${arg.type || ''} ${arg.discord_type || ''}`.toLowerCase();
  const label = `${arg.name}${arg.required ? ' *' : ''}`;
  if ((arg.choices || []).length) {
    return `<label>${esc(label)}<select data-arg="${esc(arg.name)}"><option value="">اختر...</option>${arg.choices.map(choice => `<option value="${esc(choice.value)}" ${String(choice.value) === String(value) ? 'selected' : ''}>${esc(choice.name)}${String(choice.value) !== String(choice.name) ? ` — ${esc(choice.value)}` : ''}</option>`).join('')}</select></label>`;
  }
  if (type.includes('bool')) {
    return `<label>${esc(label)}<select data-arg="${esc(arg.name)}"><option value="false" ${String(value) === 'false' ? 'selected' : ''}>لا</option><option value="true" ${String(value) === 'true' ? 'selected' : ''}>نعم</option></select></label>`;
  }
  const numeric = type.includes('int') || type.includes('float');
  const objectHint = type.includes('member') || type.includes('user') || type.includes('role') || type.includes('channel')
    ? '<small class="field-hint">أدخل Discord ID — يمكن نسخه من السيرفر.</small>' : '';
  const longText = ['message', 'prompt', 'text', 'description', 'topic', 'reason'].includes(arg.name);
  const control = longText
    ? `<textarea data-arg="${esc(arg.name)}" placeholder="${esc(arg.type || 'value')}">${esc(value)}</textarea>`
    : `<input data-arg="${esc(arg.name)}" ${numeric ? `type="${type.includes('float') ? 'number' : 'number'}" ${type.includes('float') ? 'step="any"' : 'step="1"'}` : ''} value="${esc(value)}" placeholder="${esc(arg.type || arg.discord_type || 'value')}">`;
  return `<label>${esc(label)}${control}${objectHint}</label>`;
}

async function openCommand(name, presets = {}) {
  const wanted = commandPath(name);
  let command = commands.find(item => commandPath(item.name) === wanted);
  if (!command) {
    await loadCommands();
    command = commands.find(item => commandPath(item.name) === wanted);
    if (!command) {
      toast('هذا الزر قديم أو الأمر غير محمّل حاليًا — حدّث الكتالوج أولًا.', 'error');
      return;
    }
  }
  name = command.name;
  $('#modalTitle').textContent = `/${name.replace('.', ' / ')}`;
  $('#modalDescription').textContent = command.description || 'سيتم استدعاء الأمر الحقيقي بصلاحيات جلسة الإدارة.';
  $('#modalFields').innerHTML = command.args?.length
    ? command.args.map(arg => inputForArg(arg, presets[arg.name])).join('')
    : '<div class="empty-fields">هذا الأمر لا يحتاج معاملات.</div>';
  $('#modalResult').textContent = '';
  $('#modal').classList.remove('hidden');
  $('#modalRun').onclick = async () => {
    const args = {};
    $$('[data-arg]', $('#modalFields')).forEach(field => {
      if (field.value !== '') args[field.dataset.arg] = field.value;
    });
    const missing = (command.args || []).find(arg => arg.required && !args[arg.name]);
    if (missing) {
      $('#modalResult').className = 'modal-result error';
      $('#modalResult').textContent = `المعامل ${missing.name} مطلوب`;
      return;
    }
    const button = $('#modalRun');
    button.disabled = true;
    button.innerHTML = 'جاري التنفيذ…';
    try {
      const data = await API.post('/api/commands/execute', {command: name, args});
      $('#modalResult').className = 'modal-result success';
      $('#modalResult').textContent = data.message || 'تم التنفيذ بنجاح.';
      toast('تم تنفيذ الأمر بنجاح');
      stats();
    } catch (error) {
      $('#modalResult').className = 'modal-result error';
      $('#modalResult').textContent = error.message;
    } finally {
      button.disabled = false;
      button.innerHTML = 'تنفيذ الأمر <span>←</span>';
    }
  };
}

function closeModal() { $('#modal').classList.add('hidden'); }
$('#modalClose').onclick = closeModal;
$('#modalCancel').onclick = closeModal;
$('#modal').onclick = event => { if (event.target.id === 'modal') closeModal(); };
document.addEventListener('keydown', event => { if (event.key === 'Escape') closeModal(); });

async function loadMembers(reset = false) {
  if (reset) { members = []; memberAfter = ''; }
  try {
    const query = encodeURIComponent($('#memberSearch').value || '');
    const data = await API.get(`/api/members?limit=50&after=${memberAfter || 0}&q=${query}`);
    members.push(...(data.members || []));
    memberAfter = data.next || '';
    $('#loadMore').disabled = !memberAfter;
    $('#membersList').innerHTML = members.map(member => `<div class="member-row"><div class="member-main"><span class="avatar small">${esc((member.name || '?').charAt(0))}</span><div><b>${esc(member.name)}</b><small>${esc(member.username)} · ${esc(member.id)}</small></div></div><button class="ghost-button" data-dm-member="${esc(member.id)}">رسالة</button></div>`).join('') || '<div class="empty-state"><span>♙</span><p>لا يوجد أعضاء مطابقون.</p></div>';
  } catch (error) {
    $('#membersList').innerHTML = `<div class="empty-state error-text">${esc(error.message)}</div>`;
  }
}
$('#memberSearch').oninput = () => {
  clearTimeout(memberTimer);
  memberTimer = setTimeout(() => loadMembers(true), 260);
};
$('#loadMore').onclick = () => loadMembers(false);
$('#sendDm').onclick = async () => {
  const id = $('#dmId').value.trim();
  const message = $('#dmText').value.trim();
  if (!id || !message) return setResult('#dmResult', 'أدخل ID ورسالة قبل الإرسال.', 'error');
  try {
    const data = await API.post('/api/commands/execute', {command: 'member.dm', args: {member: id, message}});
    setResult('#dmResult', data.message || 'تم إرسال الرسالة.');
    toast('تم إرسال الرسالة');
    $('#dmText').value = '';
  } catch (error) { setResult('#dmResult', error.message, 'error'); }
};

async function loadMarket() {
  try {
    const data = await API.get('/api/market');
    $('#marketList').innerHTML = (data.stocks || []).map(stock => `<div class="market-row"><span class="market-symbol">${esc(stock.emoji)}<b>${esc(stock.symbol)}</b><small>${esc(stock.name)}</small></span><span><b>${number(stock.price)}</b><small class="${stock.change >= 0 ? 'positive' : 'negative'}">${stock.change >= 0 ? '+' : ''}${stock.change}%</small></span></div>`).join('') || '<div class="empty-state">لا توجد أسهم مضبوطة.</div>';
    $('#loansList').innerHTML = (data.loans || []).map(loan => `<tr><td>#${esc(loan.id)}</td><td>${esc(loan.borrower)}</td><td>${number(loan.total)}</td><td>${number(loan.paid)}</td><td><span class="status-tag ${esc(loan.status)}">${esc(loan.status)}</span></td><td>${date(loan.due)}</td></tr>`).join('') || '<tr><td colspan="6" class="empty-cell">لا توجد قروض.</td></tr>';
  } catch (error) { toast(error.message, 'error'); }
}
$('#refreshMarket').onclick = loadMarket;
$('#moneySubmit').onclick = async () => {
  const user_id = $('#moneyUser').value.trim();
  const action = $('#moneyAction').value;
  const amount = Number($('#moneyAmount').value || 0);
  if (!/^\d+$/.test(user_id)) return setResult('#moneyResult', 'أدخل Discord ID صحيحًا.', 'error');
  if (action !== 'reset' && amount <= 0) return setResult('#moneyResult', 'المبلغ يجب أن يكون أكبر من صفر.', 'error');
  try {
    const data = await API.post('/api/money', {user_id, action, mode: $('#moneyMode').value, amount});
    setResult('#moneyResult', `تم التحديث — المحفظة: ${number(data.wallet)} · البنك: ${number(data.bank)}`);
    toast('تم تحديث رصيد العضو');
    stats();
  } catch (error) { setResult('#moneyResult', error.message, 'error'); }
};

async function loadStaff() {
  try {
    const data = await API.get('/api/staff');
    const staffMembers = data.staff || [];
    $('#staffList').innerHTML = staffMembers.length ? staffMembers.map(staff => {
      const id = String(staff.id || '');
      return `<div class="staff-row"><span class="avatar small">${esc((staff.name || id || '?').charAt(0))}</span><div><b>${esc(staff.name || `عضو ${id.slice(-4)}`)}</b><small>${esc(id)} · ${esc(rankNames[staff.rank] || staff.rank)}</small></div><button class="ghost-button danger-text" data-remove-staff="${esc(id)}">إزالة</button></div>`;
    }).join('') : '<div class="empty-state"><span>◇</span><p>لا يوجد أعضاء فريق مسجلون.</p></div>';
    $('#codesList').innerHTML = (data.codes || []).map(code => `<div class="code-row"><div><code>${esc(code.code)}</code><small>${esc(code.rank_name)} · ${code.used_by ? `مستخدم بواسطة ${esc(code.used_by_name || code.used_by)}` : 'متاح'}${code.revoked ? ' · مسحوب' : ''}</small></div>${!code.revoked && !code.used_by ? `<button class="ghost-button danger-text" data-revoke-code="${esc(code.code)}">سحب</button>` : ''}</div>`).join('') || '<div class="empty-state">لا توجد أكواد.</div>';
  } catch (error) { toast(error.message, 'error'); }
}
$('#refreshStaff').onclick = loadStaff;
let staffWarningTimer;
$('#staffWarningSearch').oninput = () => {
  clearTimeout(staffWarningTimer);
  staffWarningTimer = setTimeout(loadStaffWarnings, 250);
};
$('#refreshStaffWarnings').onclick = loadStaffWarnings;
async function loadStaffWarnings() {
  try {
    const query = encodeURIComponent($('#staffWarningSearch').value || '');
    const data = await API.get(`/api/moderation/warnings?limit=100&q=${query}`);
    $('#staffWarnings').innerHTML = (data.warnings || []).map(warning => `<tr><td>${esc(warning.member_name)} · ${esc(warning.user_id)}</td><td>${esc(warning.moderator_name)} · ${esc(warning.moderator_id)}</td><td>${esc(warning.reason)}</td><td>${date(warning.timestamp)}</td></tr>`).join('') || '<tr><td colspan="4" class="empty-cell">لا توجد تحذيرات محفوظة.</td></tr>';
  } catch (error) {
    $('#staffWarnings').innerHTML = `<tr><td colspan="4" class="empty-cell error-text">${esc(error.message)}</td></tr>`;
  }
}
$('#generateCode').onclick = async () => {
  try {
    const data = await API.post('/api/staff/code', {rank: $('#codeRank').value, count: Number($('#codeCount').value || 1), note: $('#codeNote').value.trim()});
    $('#generatedCodes').innerHTML = (data.codes || []).map(code => `<code>${esc(code)}</code>`).join('');
    toast('تم توليد كود التفعيل');
    loadStaff();
  } catch (error) { setResult('#generatedCodes', error.message, 'error'); }
};
async function revokeCode(code) {
  if (!confirm(`سحب الكود ${code}؟`)) return;
  try { await API.post('/api/staff/revoke', {code}); toast('تم سحب الكود'); loadStaff(); }
  catch (error) { toast(error.message, 'error'); }
}
async function removeStaff(user_id) {
  if (!confirm('إزالة رتبة هذا العضو؟')) return;
  try { await API.post('/api/staff/remove', {user_id}); toast('تمت إزالة العضو من الفريق'); loadStaff(); }
  catch (error) { toast(error.message, 'error'); }
}

async function loadLogs() {
  try {
    const data = await API.get('/api/logs');
    $('#logsOut').innerHTML = (data.logs || []).map(log => `<div class="log-row"><span class="log-icon">${log.source === 'dashboard' ? '⌘' : '◈'}</span><div><b>${esc(log.action)}</b><p>${esc(log.details)}</p><small>${esc(log.actor_name || log.actor_id)} · ${date(log.ts)}</small></div><span class="log-source">${esc(log.source || 'discord')}</span></div>`).join('') || '<div class="empty-state"><span>≡</span><p>لا توجد عمليات مسجلة بعد.</p></div>';
  } catch (error) { $('#logsOut').innerHTML = `<div class="empty-state error-text">${esc(error.message)}</div>`; }
}
$('#refreshLogs').onclick = loadLogs;

async function loadAIModerationLogs() {
  try {
    const data = await API.get('/api/ai/moderation/logs?limit=100');
    $('#aiModLogs').innerHTML = (data.logs || []).map(log => `<tr><td>${date(log.timestamp)}</td><td>${esc(log.user_id)}</td><td>${esc(log.classification)}${log.confidence == null ? '' : ` · ${Math.round(Number(log.confidence) * 100)}%`}</td><td>${esc(log.action_requested || '—')}</td><td>${esc(log.result)}</td><td>${esc(log.reason)}</td></tr>`).join('') || '<tr><td colspan="6" class="empty-cell">لا توجد قرارات محفوظة.</td></tr>';
  } catch (error) {
    $('#aiModLogs').innerHTML = `<tr><td colspan="6" class="empty-cell error-text">${esc(error.message)}</td></tr>`;
  }
}
$('#refreshAiLogs').onclick = loadAIModerationLogs;

async function loadSettings() {
  try {
    const data = await API.get('/api/settings');
    $('#settingsEditor').value = JSON.stringify(data.settings || {}, null, 2);
  } catch (error) { setResult('#settingsResult', error.message, 'error'); }
}
$('#saveSettings').onclick = async () => {
  try {
    const settings = JSON.parse($('#settingsEditor').value);
    await API.post('/api/settings', {settings});
    setResult('#settingsResult', 'تم حفظ الإعدادات بنجاح.');
    toast('تم حفظ الإعدادات');
  } catch (error) { setResult('#settingsResult', error.message.includes('JSON') ? 'صيغة JSON غير صالحة.' : error.message, 'error'); }
};

async function loadAtria() {
  try {
    const data = await API.get('/api/ai/moderation/settings');
    const moderation = data.config || {};
    const legacy = data.legacy || {};
    const channels = (data.guilds || []).flatMap(guild => (guild.channels || []).map(channel =>
      `<option value="${esc(channel.id)}">${esc(guild.name)} · #${esc(channel.name)}</option>`
    )).join('');
    $('#aiModChannels').innerHTML = channels || '<option disabled>لا توجد قنوات نصية متاحة</option>';
    const allowedChannels = new Set((moderation.channels || []).map(String));
    $$('option', $('#aiModChannels')).forEach(option => { option.selected = allowedChannels.has(option.value); });
    const detections = new Set((moderation.detection_types || ['spam', 'flood', 'repeated_messages', 'toxicity', 'harassment']).map(String));
    $$('option', $('#aiModDetections')).forEach(option => { option.selected = detections.has(option.value); });
    const actions = new Set((moderation.automatic_actions || []).map(String));
    $('#aiModEnabled').checked = Boolean(moderation.enabled ?? legacy.moderation_enabled) && !moderation.kill_switch;
    $('#aiModSensitivity').value = moderation.sensitivity || 'medium';
    $('#aiModMaxActions').value = moderation.max_actions_per_hour || 20;
    $('#aiTimeout').value = moderation.timeout_minutes || legacy.timeout_minutes || 10;
    $('#aiActionWarn').checked = actions.has('warn');
    $('#aiActionTimeout').checked = actions.has('timeout');
    $('#aiActionBan').checked = actions.has('ban');
    $('#aiAllowBan').checked = moderation.allow_ai_ban === true;
    await Promise.all([loadAIStatus(), loadAIModerationLogs()]);
  } catch (error) { setResult('#aiSaveOut', error.message, 'error'); }
}
async function loadAIStatus() {
  try {
    const data = await API.get('/api/ai/status');
    const provider = data.provider || {};
    $('#aiProviderStatus').textContent = `${provider.provider || 'AI'} · ${provider.status || 'Unknown'}`;
    const details = [provider.model, provider.latency_ms == null ? null : `${provider.latency_ms} ms`, provider.last_request, provider.error]
      .filter(Boolean).join(' · ');
    $('#aiProviderDetails').textContent = details || 'لم يُجر اتصال بالمزود بعد.';
    $('#aiProviderDetails').classList.toggle('error', provider.status === 'Error' || provider.status === 'Not Configured');
  } catch (error) {
    $('#aiProviderStatus').textContent = 'Unavailable';
    setResult('#aiProviderDetails', error.message, 'error');
  }
}
async function runAtria(mode) {
  const prompt = $('#aiPrompt').value.trim();
  if (!prompt) return setResult('#aiOut', 'اكتب طلبًا أولًا.', 'error');
  $('#aiOut').textContent = 'جاري التحليل…';
  try { const data = await API.post('/api/atria', {prompt, mode}); $('#aiOut').textContent = data.answer || 'لم تصل نتيجة.'; }
  catch (error) { $('#aiOut').textContent = `❌ ${error.message}`; }
}
$('#aiModerate').onclick = () => runAtria('moderation');
$('#aiTestProvider').onclick = async () => {
  const button = $('#aiTestProvider');
  button.disabled = true;
  try {
    await API.post('/api/ai/test', {});
    await loadAIStatus();
  } catch (error) {
    setResult('#aiProviderDetails', error.message, 'error');
  } finally { button.disabled = false; }
};
$('#aiKill').onclick = async () => {
  const button = $('#aiKill');
  button.disabled = true;
  try {
    await API.post('/api/ai/moderation/kill', {});
    $('#aiModEnabled').checked = false;
    setResult('#aiSaveOut', 'تم تعطيل AI Moderation فورًا.');
    toast('تم تفعيل مفتاح إيقاف AI Moderation');
    await loadAIStatus();
  } catch (error) { setResult('#aiSaveOut', error.message, 'error'); }
  finally { button.disabled = false; }
};
$('#saveAi').onclick = async () => {
  const automatic_actions = [
    $('#aiActionWarn').checked ? 'warn' : null,
    $('#aiActionTimeout').checked ? 'timeout' : null,
    $('#aiActionBan').checked ? 'ban' : null
  ].filter(Boolean);
  try {
    await API.post('/api/ai/moderation/settings', {
      enabled: $('#aiModEnabled').checked,
      channels: selectedValues('#aiModChannels'),
      detection_types: selectedValues('#aiModDetections'),
      sensitivity: $('#aiModSensitivity').value,
      automatic_actions,
      allow_ai_ban: $('#aiAllowBan').checked,
      max_actions_per_hour: Number($('#aiModMaxActions').value),
      timeout_minutes: Number($('#aiTimeout').value)
    });
    setResult('#aiSaveOut', 'تم حفظ إعدادات المراقبة.');
    toast('تم حفظ AI Moderation policy');
    await Promise.all([loadAIStatus(), loadAIModerationLogs()]);
  } catch (error) { setResult('#aiSaveOut', error.message, 'error'); }
};

function selectedValues(selector) {
  return $$('option:checked', $(selector)).map(option => option.value);
}
let voiceGuilds = [];
async function loadVoice() {
  try {
    const data = await API.get('/api/voice/targets');
    voiceGuilds = data.guilds || [];
    $('#voiceGuild').innerHTML = voiceGuilds.map(guild => `<option value="${esc(guild.id)}">${esc(guild.name)}</option>`).join('') || '<option value="">No connected servers</option>';
    renderVoiceChannels();
    const current = voiceGuilds.find(guild => guild.id === $('#voiceGuild').value);
    $('#voiceStatus').textContent = current?.connected_channel_id
      ? `Connected · ${current.channels.find(channel => channel.id === current.connected_channel_id)?.name || current.connected_channel_id}`
      : 'Disconnected';
  } catch (error) { setResult('#voiceStatus', error.message, 'error'); }
}
function renderVoiceChannels() {
  const guild = voiceGuilds.find(item => item.id === $('#voiceGuild').value);
  const channels = guild?.channels || [];
  $('#voiceChannel').innerHTML = channels.map(channel => `<option value="${esc(channel.id)}">${esc(channel.name)}</option>`).join('') || '<option value="">No voice channels</option>';
}
$('#voiceGuild').onchange = renderVoiceChannels;
$('#refreshVoice').onclick = loadVoice;
async function voiceAction(action) {
  const guild_id = $('#voiceGuild').value;
  const channel_id = $('#voiceChannel').value;
  if (!guild_id) return setResult('#voiceStatus', 'لا يوجد سيرفر متاح.', 'error');
  try {
    const data = await API.post('/api/voice/action', {action, guild_id, channel_id});
    setResult('#voiceStatus', `${action === 'join' ? 'Connected' : 'Disconnected'}${data.channel_name ? ` · ${data.channel_name}` : ''}`);
    await loadVoice();
  } catch (error) { setResult('#voiceStatus', error.message, 'error'); }
}
$('#voiceJoin').onclick = () => voiceAction('join');
$('#voiceLeave').onclick = () => voiceAction('leave');
async function loadAIChat() {
  try {
    const data = await API.get('/api/ai/chat/settings');
    const config = data.config || {};
    const options = (data.guilds || []).flatMap(guild => (guild.channels || []).map(channel =>
      `<option value="${esc(channel.id)}" data-guild="${esc(guild.name)}">${esc(guild.name)} · #${esc(channel.name)}</option>`
    )).join('');
    $('#aiChatChannels').innerHTML = options || '<option disabled>لا توجد قنوات نصية متاحة</option>';
    $('#aiChatIgnored').innerHTML = options || '<option disabled>لا توجد قنوات نصية متاحة</option>';
    const allowed = new Set((config.allowed_channels || []).map(String));
    const ignored = new Set((config.ignored_channels || []).map(String));
    $$('option', $('#aiChatChannels')).forEach(option => { option.selected = allowed.has(option.value); });
    $$('option', $('#aiChatIgnored')).forEach(option => { option.selected = ignored.has(option.value); });
    const logChannels = (data.guilds || []).flatMap(guild => (guild.channels || [])
      .filter(channel => allowed.has(String(channel.id)))
      .map(channel => `<option value="${esc(channel.id)}">${esc(guild.name)} · #${esc(channel.name)}</option>`));
    $('#aiChatLogChannel').innerHTML = logChannels.join('') || '<option value="">لا توجد قناة مسموحة</option>';
    $('#aiChatEnabled').checked = Boolean(config.enabled);
    $('#aiChatMode').value = config.response_mode || 'mention_only';
    $('#aiChatLanguage').value = config.language || 'auto';
    $('#aiChatContext').value = String(config.context_messages || 10);
    $('#aiChatRetention').value = config.retention_days || 30;
    $('#aiChatTunisian').checked = Boolean(config.tunisian_mode);
    $('#aiChatName').value = config.name || 'Vixen';
    $('#aiChatTone').value = config.tone || 'friendly';
    $('#aiChatStyle').value = config.style || 'conversational';
    $('#aiChatPrompt').value = config.system_prompt || '';
    $('#aiChatUserCooldown').value = config.user_cooldown_seconds ?? 5;
    $('#aiChatChannelCooldown').value = config.channel_cooldown_seconds ?? 2;
    $('#aiChatGlobalCooldown').value = config.global_cooldown_seconds ?? 1;
    if (logChannels.length) await loadAIChatLogs();
    else $('#aiChatLogs').innerHTML = '<tr><td colspan="3" class="empty-cell">لا توجد قنوات AI Chat مسموحة.</td></tr>';
  } catch (error) { setResult('#aiChatSaveResult', error.message, 'error'); }
}
$('#saveAIChat').onclick = async () => {
  const payload = {
    enabled: $('#aiChatEnabled').checked,
    allowed_channels: selectedValues('#aiChatChannels'),
    ignored_channels: selectedValues('#aiChatIgnored'),
    response_mode: $('#aiChatMode').value,
    language: $('#aiChatLanguage').value,
    context_messages: Number($('#aiChatContext').value),
    retention_days: Number($('#aiChatRetention').value),
    tunisian_mode: $('#aiChatTunisian').checked,
    name: $('#aiChatName').value.trim(),
    tone: $('#aiChatTone').value.trim(),
    style: $('#aiChatStyle').value.trim(),
    system_prompt: $('#aiChatPrompt').value,
    user_cooldown_seconds: Number($('#aiChatUserCooldown').value),
    channel_cooldown_seconds: Number($('#aiChatChannelCooldown').value),
    global_cooldown_seconds: Number($('#aiChatGlobalCooldown').value)
  };
  try {
    await API.post('/api/ai/chat/settings', payload);
    setResult('#aiChatSaveResult', 'تم حفظ إعدادات AI Chat.');
    toast('تم حفظ إعدادات AI Chat');
  } catch (error) { setResult('#aiChatSaveResult', error.message, 'error'); }
};
async function loadAIChatLogs() {
  const channelId = $('#aiChatLogChannel').value;
  if (!channelId) return;
  try {
    const data = await API.get(`/api/ai/chat/logs?channel_id=${encodeURIComponent(channelId)}&limit=100`);
    $('#aiChatLogs').innerHTML = (data.messages || []).map(message => `<tr><td>${date(message.timestamp)}</td><td>${message.role === 'assistant' ? 'Vixen' : esc(message.user_id)}</td><td class="dm-log-content">${esc(message.content)}</td></tr>`).join('') || '<tr><td colspan="3" class="empty-cell">لا توجد محادثات محفوظة.</td></tr>';
  } catch (error) { $('#aiChatLogs').innerHTML = `<tr><td colspan="3" class="empty-cell error-text">${esc(error.message)}</td></tr>`; }
}
$('#aiChatLogChannel').onchange = loadAIChatLogs;
$('#refreshAIChatLogs').onclick = loadAIChatLogs;
$('#testAIChat').onclick = async () => {
  const prompt = $('#aiChatTestPrompt').value.trim();
  if (!prompt) return setResult('#aiChatTestResult', 'اكتب رسالة للاختبار.', 'error');
  $('#testAIChat').disabled = true;
  $('#aiChatTestResult').textContent = 'جارٍ الاتصال بالمزود…';
  try {
    const data = await API.post('/api/ai/chat/test', {prompt});
    $('#aiChatTestResult').textContent = data.answer || '';
  } catch (error) { setResult('#aiChatTestResult', error.message, 'error'); }
  finally { $('#testAIChat').disabled = false; }
};

function buildDMDesign() {
  const fields = $$('.dm-field-row', $('#dmFields')).map(row => ({
    name: $('.dm-field-name', row).value.trim(),
    value: $('.dm-field-value', row).value.trim(),
    inline: $('.dm-field-inline', row).checked
  })).filter(field => field.name || field.value);
  const authorName = $('#dmAuthor').value.trim();
  const author = authorName ? {name: authorName} : null;
  if (author && $('#dmAuthorUrl').value.trim()) author.url = $('#dmAuthorUrl').value.trim();
  if (author && $('#dmAuthorIcon').value.trim()) author.icon_url = $('#dmAuthorIcon').value.trim();
  const design = {
    title: $('#dmTitle').value.trim(),
    description: $('#dmDescription').value.trim(),
    color: $('#dmColor').value,
    thumbnail: $('#dmThumbnail').value.trim(),
    image: $('#dmImage').value.trim(),
    fields,
    footer: $('#dmFooterIcon').value.trim()
      ? {text: $('#dmFooter').value.trim(), icon_url: $('#dmFooterIcon').value.trim()}
      : $('#dmFooter').value.trim(),
    timestamp: $('#dmTimestamp').checked
  };
  if (author) design.author = author;
  return design;
}

function addDMField(field = {}) {
  const row = document.createElement('div');
  row.className = 'dm-field-row form-grid';
  row.innerHTML = `<label>Field name<input class="dm-field-name" maxlength="256" value="${esc(field.name || '')}"></label><label>Field value<textarea class="dm-field-value" maxlength="1024">${esc(field.value || '')}</textarea></label><label class="dm-inline-option"><input class="dm-field-inline" type="checkbox" ${field.inline ? 'checked' : ''}> Inline</label><button type="button" class="icon-button" data-dm-remove-field aria-label="Remove field">×</button>`;
  $('#dmFields').appendChild(row);
}

function applyDMDesign(design = {}) {
  $('#dmTitle').value = design.title || '';
  $('#dmDescription').value = design.description || '';
  $('#dmColor').value = /^#[0-9a-fA-F]{6}$/.test(design.color || '') ? design.color : '#f5a623';
  const author = design.author || {};
  $('#dmAuthor').value = author.name || '';
  $('#dmAuthorUrl').value = author.url || '';
  $('#dmAuthorIcon').value = author.icon_url || '';
  $('#dmThumbnail').value = design.thumbnail?.url || design.thumbnail || '';
  $('#dmImage').value = design.image?.url || design.image || '';
  $('#dmFooter').value = design.footer?.text || design.footer || '';
  $('#dmFooterIcon').value = design.footer?.icon_url || '';
  $('#dmTimestamp').checked = Boolean(design.timestamp);
  $('#dmFields').replaceChildren();
  (design.fields || []).forEach(field => addDMField(field));
  scheduleDMPreview();
}

function scheduleDMPreview() {
  clearTimeout(dmPreviewTimer);
  dmPreviewTimer = setTimeout(renderDMPreview, 450);
}

async function loadDMCenter() {
  try {
    const data = await API.get('/api/dm/center');
    const previousGuild = $('#dmGuild').value;
    dmGuilds = data.guilds || [];
    $('#dmGuild').innerHTML = dmGuilds.map(guild => `<option value="${esc(guild.id)}">${esc(guild.name)} · ${number(guild.member_count)}</option>`).join('') || '<option value="">No connected servers</option>';
    if (dmGuilds.some(guild => guild.id === previousGuild)) $('#dmGuild').value = previousGuild;
    await Promise.all([loadDMTemplates(), loadDMHistory()]);
    if ($('#dmMemberSearch').value.trim().length >= 2) await loadDMMembers();
  } catch (error) { setResult('#dmCenterResult', error.message, 'error'); }
}

async function loadDMMembers() {
  const guildId = $('#dmGuild').value;
  const query = $('#dmMemberSearch').value.trim();
  if (!guildId || query.length < 2) {
    $('#dmMemberResults').innerHTML = '<div class="empty-state">اكتب حرفين على الأقل للبحث.</div>';
    return;
  }
  try {
    const data = await API.get(`/api/dm/members?guild_id=${encodeURIComponent(guildId)}&q=${encodeURIComponent(query)}`);
    dmMembers = data.members || [];
    $('#dmMemberResults').innerHTML = dmMembers.map(member => `<button type="button" class="member-row dm-member-choice" data-dm-center-member="${esc(member.id)}"><span class="member-main"><img class="avatar small" src="${esc(member.avatar)}" alt=""><span><b>${esc(member.display_name)}</b><small>${esc(member.username)} · ${esc(member.id)} · ${esc(member.roles.join(', ') || (member.is_bot ? 'Bot' : 'User'))}</small></span></span></button>`).join('') || '<div class="empty-state">لا يوجد عضو مطابق.</div>';
  } catch (error) { $('#dmMemberResults').innerHTML = `<div class="empty-state error-text">${esc(error.message)}</div>`; }
}

async function loadDMTemplates() {
  const guildId = $('#dmGuild').value;
  if (!guildId) return;
  const data = await API.get(`/api/dm/templates?guild_id=${encodeURIComponent(guildId)}`);
  dmTemplates = data.templates || [];
  $('#dmTemplate').innerHTML = `<option value="">تصميم جديد</option>${dmTemplates.map(template => `<option value="${esc(template.template_id)}">${esc(template.name)}</option>`).join('')}`;
  if (dmTemplates.some(template => String(template.template_id) === String(currentDmTemplateId))) {
    $('#dmTemplate').value = String(currentDmTemplateId);
  } else {
    currentDmTemplateId = '';
    $('#dmTemplate').value = '';
  }
}

async function loadDMHistory() {
  const guildId = $('#dmGuild').value;
  if (!guildId) return;
  try {
    const data = await API.get(`/api/dm/history?guild_id=${encodeURIComponent(guildId)}&limit=100`);
    $('#dmHistory').innerHTML = (data.history || []).map(item => `<tr><td>${esc(item.recipient_id)}</td><td>${item.template_id ? `#${esc(item.template_id)}` : 'Custom'}</td><td>${esc(item.sender_id)}</td><td><span class="status-tag ${esc(item.status)}">${esc(item.status)}</span></td><td>${esc(item.error || '—')}</td><td>${date(item.timestamp)}</td></tr>`).join('') || '<tr><td colspan="6" class="empty-cell">لا يوجد سجل إرسال.</td></tr>';
  } catch (error) { $('#dmHistory').innerHTML = `<tr><td colspan="6" class="empty-cell error-text">${esc(error.message)}</td></tr>`; }
}

async function renderDMPreview() {
  const guildId = $('#dmGuild').value;
  const memberId = $('#dmRecipientId').value.trim();
  if (!guildId || !/^\d+$/.test(memberId)) {
    $('#dmPreview').textContent = 'اختر سيرفرًا وعضوًا لمعاينة المتغيرات.';
    return null;
  }
  try {
    const body = {guild_id: guildId, member_id: memberId, reason: $('#dmReason').value, embed: buildDMDesign()};
    if (currentDmTemplateId) body.template_id = currentDmTemplateId;
    const data = await API.post('/api/dm/preview', body);
    const embed = data.embed;
    const color = `#${Number(embed.color || 0xf5a623).toString(16).padStart(6, '0')}`;
    const author = embed.author?.name ? `<b>${esc(embed.author.name)}</b><br>` : '';
    const fields = (embed.fields || []).map(field => `<div class="dm-preview-field"><b>${esc(field.name)}</b><p>${esc(field.value)}</p></div>`).join('');
    const image = embed.image?.url ? `<img class="dm-preview-image" src="${esc(embed.image.url)}" alt="">` : '';
    const thumbnail = embed.thumbnail?.url ? `<img class="dm-preview-thumbnail" src="${esc(embed.thumbnail.url)}" alt="">` : '';
    $('#dmPreview').innerHTML = `<div class="dm-preview-card" style="border-color:${esc(color)}">${author}${embed.title ? `<h3>${esc(embed.title)}</h3>` : ''}${embed.description ? `<p>${esc(embed.description)}</p>` : ''}${thumbnail}${fields}${image}${embed.footer?.text ? `<small>${esc(embed.footer.text)}</small>` : ''}</div>`;
    return data;
  } catch (error) {
    setResult('#dmCenterResult', error.message, 'error');
    $('#dmPreview').textContent = 'تعذر إنشاء المعاينة؛ راجع قيم Embed.';
    return null;
  }
}

$('#dmGuild').onchange = async () => {
  currentDmTemplateId = '';
  await Promise.all([loadDMTemplates(), loadDMHistory()]);
  $('#dmMemberResults').innerHTML = '';
  scheduleDMPreview();
};
$('#dmMemberSearch').oninput = () => {
  clearTimeout(dmMemberTimer);
  dmMemberTimer = setTimeout(loadDMMembers, 250);
};
document.addEventListener('click', event => {
  const memberButton = event.target.closest('[data-dm-center-member]');
  if (memberButton) {
    const member = dmMembers.find(item => item.id === memberButton.dataset.dmCenterMember);
    if (!member) return;
    $('#dmRecipientId').value = member.id;
    $('#dmSelectedMember').textContent = `${member.display_name} · ${member.username} · ${member.roles.join(', ') || (member.is_bot ? 'Bot' : 'User')}`;
    scheduleDMPreview();
  }
  if (event.target.closest('[data-dm-remove-field]')) {
    event.target.closest('.dm-field-row')?.remove();
    scheduleDMPreview();
  }
});
$('#dmTemplate').onchange = () => {
  currentDmTemplateId = $('#dmTemplate').value;
  const template = dmTemplates.find(item => String(item.template_id) === String(currentDmTemplateId));
  if (template) {
    $('#dmTemplateName').value = template.name;
    applyDMDesign(template.embed);
  } else {
    $('#dmTemplateName').value = '';
    applyDMDesign({});
  }
};
$('#dmFields').addEventListener('input', scheduleDMPreview);
$('#dm-center').addEventListener('input', scheduleDMPreview);
$('#dm-center').addEventListener('change', scheduleDMPreview);
$('#dmAddField').onclick = () => { addDMField(); scheduleDMPreview(); };
$('#dmPreviewButton').onclick = renderDMPreview;
$('#refreshDM').onclick = loadDMCenter;
$('#refreshDMHistory').onclick = loadDMHistory;
$('#dmInstallTemplates').onclick = async () => {
  try {
    const result = await API.post('/api/dm/templates', {guild_id: $('#dmGuild').value, action: 'install_defaults'});
    await loadDMTemplates();
    setResult('#dmCenterResult', `تمت إضافة ${result.created} قالبًا جاهزًا.`);
  } catch (error) { setResult('#dmCenterResult', error.message, 'error'); }
};
$('#dmUseBranding').onclick = () => {
  const guild = dmGuilds.find(item => item.id === $('#dmGuild').value);
  if (!guild) return setResult('#dmCenterResult', 'اختر سيرفرًا أولًا.', 'error');
  $('#dmAuthor').value = guild.name;
  $('#dmAuthorIcon').value = guild.icon || '';
  $('#dmColor').value = /^#[0-9a-fA-F]{6}$/.test(guild.primary_color || '') ? guild.primary_color : '#f5a623';
  $('#dmFooter').value = '{server} · {timestamp}';
  scheduleDMPreview();
};
$('#dmUseBotBranding').onclick = () => {
  const guild = dmGuilds.find(item => item.id === $('#dmGuild').value);
  if (!guild) return setResult('#dmCenterResult', 'اختر سيرفرًا أولًا.', 'error');
  $('#dmAuthor').value = guild.bot_name || 'Vixen';
  $('#dmAuthorIcon').value = guild.bot_avatar || '';
  $('#dmColor').value = /^#[0-9a-fA-F]{6}$/.test(guild.accent_color || '') ? guild.accent_color : '#2ecc71';
  scheduleDMPreview();
};
$('#dmSaveTemplate').onclick = async () => {
  const name = $('#dmTemplateName').value.trim();
  try {
    const payload = {guild_id: $('#dmGuild').value, action: 'save', name, embed: buildDMDesign()};
    if (currentDmTemplateId) payload.template_id = currentDmTemplateId;
    const result = await API.post('/api/dm/templates', payload);
    currentDmTemplateId = String(result.template_id);
    await loadDMTemplates();
    $('#dmTemplate').value = currentDmTemplateId;
    setResult('#dmCenterResult', 'تم حفظ القالب.');
    await loadDMHistory();
  } catch (error) { setResult('#dmCenterResult', error.message, 'error'); }
};
$('#dmDuplicateTemplate').onclick = async () => {
  if (!currentDmTemplateId) return setResult('#dmCenterResult', 'اختر قالبًا لنسخه.', 'error');
  const name = `${$('#dmTemplateName').value.trim()} copy`.trim();
  try {
    const result = await API.post('/api/dm/templates', {guild_id: $('#dmGuild').value, action: 'duplicate', template_id: currentDmTemplateId, name});
    currentDmTemplateId = String(result.template_id);
    await loadDMTemplates();
    $('#dmTemplate').value = currentDmTemplateId;
    $('#dmTemplateName').value = name;
    toast('تم نسخ القالب');
  } catch (error) { setResult('#dmCenterResult', error.message, 'error'); }
};
$('#dmDeleteTemplate').onclick = async () => {
  if (!currentDmTemplateId) return setResult('#dmCenterResult', 'اختر قالبًا لحذفه.', 'error');
  if (!confirm('حذف القالب المحدد؟')) return;
  try {
    await API.post('/api/dm/templates', {guild_id: $('#dmGuild').value, action: 'delete', template_id: currentDmTemplateId});
    currentDmTemplateId = '';
    $('#dmTemplateName').value = '';
    applyDMDesign({});
    await loadDMTemplates();
    toast('تم حذف القالب');
  } catch (error) { setResult('#dmCenterResult', error.message, 'error'); }
};
$('#dmSendButton').onclick = async () => {
  const preview = await renderDMPreview();
  if (!preview) return;
  if (!confirm(`إرسال هذا الـEmbed إلى ${preview.recipient.name}؟`)) return;
  const button = $('#dmSendButton');
  button.disabled = true;
  try {
    const payload = {
      guild_id: $('#dmGuild').value,
      member_id: $('#dmRecipientId').value.trim(),
      confirm: true,
      preview_token: preview.preview_token
    };
    const result = await API.post('/api/dm/send', payload);
    setResult('#dmCenterResult', `تم الإرسال فعليًا · سجل #${result.dm_id}`);
    toast('تم إرسال DM');
  } catch (error) { setResult('#dmCenterResult', error.message, 'error'); }
  finally { button.disabled = false; await loadDMHistory(); }
};

function uptimeLabel(seconds) {
  if (!Number.isFinite(Number(seconds))) return '—';
  let remaining = Math.max(0, Math.floor(Number(seconds)));
  const days = Math.floor(remaining / 86400); remaining %= 86400;
  const hours = Math.floor(remaining / 3600); remaining %= 3600;
  const minutes = Math.floor(remaining / 60); const secs = remaining % 60;
  return `${days}d ${hours}h ${minutes}m ${secs}s`;
}
async function loadBotProfile() {
  try {
    const data = await API.get('/api/bot/profile');
    $('#profileAvatar').src = data.avatar || '';
    $('#profileAvatar').classList.toggle('hidden', !data.avatar);
    $('#profileName').textContent = data.name || 'Bot not connected';
    $('#profileUsername').textContent = data.display_name || '';
    $('#profileId').textContent = data.id ? `ID ${data.id} · ${data.version}` : data.version;
    $('#profileConnection').textContent = data.connected ? 'Connected' : 'Not connected';
    $('#profileConnection').classList.toggle('success', Boolean(data.connected));
    $('#profileLatency').textContent = data.latency_ms == null ? '—' : `${number(data.latency_ms)} ms`;
    $('#profileUptime').textContent = uptimeLabel(data.uptime_seconds);
    $('#profileGuilds').textContent = data.guilds == null ? '—' : number(data.guilds);
    $('#profileMembers').textContent = data.members == null ? '—' : number(data.members);
    $('#profileCommands').textContent = data.commands == null ? '—' : number(data.commands);
    $('#profileAI').textContent = data.ai?.status || 'Unknown';
    $('#presenceType').value = data.presence?.type || 'playing';
    $('#presenceName').value = data.presence?.name || '';
  } catch (error) { toast(error.message, 'error'); }
}
$('#refreshBotProfile').onclick = loadBotProfile;
$('#savePresence').onclick = async () => {
  const name = $('#presenceName').value.trim();
  try {
    const result = await API.post('/api/bot/presence', {type: $('#presenceType').value, name});
    setResult('#presenceResult', result.applied ? 'Presence تطبقت على Discord.' : 'Presence محفوظة وستطبق عند اتصال Gateway.');
    await loadBotProfile();
  } catch (error) { setResult('#presenceResult', error.message, 'error'); }
};

function appendTerminalEvent(event) {
  const line = document.createElement('div');
  line.className = `terminal-line ${String(event.level || '').toLowerCase()}`;
  const timestamp = event.timestamp ? new Date(event.timestamp).toLocaleTimeString() : '';
  line.textContent = `[${timestamp}] [${event.level || 'INFO'}] ${event.logger || 'Vixen'} ${event.message || ''}`;
  $('#terminalOutput').appendChild(line);
  while ($('#terminalOutput').children.length > 300) $('#terminalOutput').firstElementChild.remove();
  $('#terminalOutput').scrollTop = $('#terminalOutput').scrollHeight;
}

function stopTerminal() {
  if (terminalAbortController) {
    const controller = terminalAbortController;
    terminalAbortController = null;
    controller.abort();
  }
  const status = $('#terminalStatus');
  if (status) status.textContent = 'Disconnected';
  const button = $('#terminalToggle');
  if (button) button.textContent = 'Start stream';
}

async function startTerminal() {
  if (terminalAbortController) return stopTerminal();
  const controller = new AbortController();
  terminalAbortController = controller;
  $('#terminalStatus').textContent = 'Connecting…';
  $('#terminalStatus').classList.remove('error');
  $('#terminalToggle').textContent = 'Stop stream';
  try {
    const response = await fetch('/api/terminal/stream', {
      headers: {Authorization: `Bearer ${API.token}`},
      signal: controller.signal
    });
    if (!response.ok) {
      let body = {};
      try { body = await response.json(); } catch (_) {}
      throw Error(body.error || `HTTP ${response.status}`);
    }
    $('#terminalStatus').textContent = 'Connected';
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffered = '';
    while (terminalAbortController === controller) {
      const {value, done} = await reader.read();
      if (done) break;
      buffered += decoder.decode(value, {stream: true});
      const blocks = buffered.split(/\r?\n\r?\n/);
      buffered = blocks.pop() || '';
      for (const block of blocks) {
        const dataLine = block.split(/\r?\n/).find(line => line.startsWith('data:'));
        if (!dataLine) continue;
        try { appendTerminalEvent(JSON.parse(dataLine.slice(5).trim())); }
        catch (_) { /* Ignore malformed event frames. */ }
      }
    }
  } catch (error) {
    if (error.name !== 'AbortError' && terminalAbortController === controller) {
      $('#terminalStatus').textContent = error.message;
      $('#terminalStatus').classList.add('error');
    }
  } finally {
    if (terminalAbortController === controller) {
      terminalAbortController = null;
      $('#terminalToggle').textContent = 'Start stream';
      if ($('#terminalStatus').textContent === 'Connected') $('#terminalStatus').textContent = 'Disconnected';
    }
  }
}
$('#terminalToggle').onclick = startTerminal;

boot();