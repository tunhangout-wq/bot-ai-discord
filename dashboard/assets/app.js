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
    atria: 'Atria AI', logs: 'سجل التدقيق', settings: 'الإعدادات'
  };
  $('#title').textContent = titles[id] || id;
  $('#app').classList.remove('menu-open');
  if (id === 'overview') stats();
  if (id === 'commands') renderCommands();
  if (id === 'members') loadMembers(true);
  if (id === 'economy') loadMarket();
  if (id === 'staff') loadStaff();
  if (id === 'atria') loadAtria();
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
    const data = await API.get('/api/settings');
    const atria = data.settings?.atria || {};
    $('#aiModEnabled').checked = Boolean(atria.moderation_enabled);
    $('#aiModMode').value = atria.moderation_mode || (atria.moderation_prefixes?.length ? 'prefix' : 'all');
    $('#aiTimeout').value = atria.timeout_minutes || 10;
  } catch (error) { setResult('#aiSaveOut', error.message, 'error'); }
}
async function runAtria(mode) {
  const prompt = $('#aiPrompt').value.trim();
  if (!prompt) return setResult('#aiOut', 'اكتب طلبًا أولًا.', 'error');
  $('#aiOut').textContent = 'جاري التحليل…';
  try { const data = await API.post('/api/atria', {prompt, mode}); $('#aiOut').textContent = data.answer || 'لم تصل نتيجة.'; }
  catch (error) { $('#aiOut').textContent = `❌ ${error.message}`; }
}
$('#aiSend').onclick = () => runAtria('chat');
$('#aiModerate').onclick = () => runAtria('moderation');
$('#saveAi').onclick = async () => {
  try {
    const data = await API.get('/api/settings');
    const settings = data.settings || {};
    settings.atria = {...(settings.atria || {}), moderation_enabled: $('#aiModEnabled').checked, moderation_mode: $('#aiModMode').value, timeout_minutes: Math.max(1, Math.min(60, Number($('#aiTimeout').value) || 10))};
    await API.post('/api/settings', {settings});
    setResult('#aiSaveOut', 'تم حفظ إعدادات المراقبة.');
    toast('تم حفظ إعدادات Atria');
  } catch (error) { setResult('#aiSaveOut', error.message, 'error'); }
};

boot();