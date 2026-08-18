// Recording SCORM API stub — 2004 (API_1484_11) + 1.2 (API). Lives on the launcher (parent) window.
(function () {
  const log = [];
  window.__scormLog = log;
  const cmi = {
    'cmi.completion_status': 'unknown',
    'cmi.success_status': 'unknown',
    'cmi.entry': 'ab-initio',
    'cmi.exit': '',
    'cmi.location': '',
    'cmi.suspend_data': '',
    'cmi.mode': 'normal',
    'cmi.credit': 'credit',
    'cmi.learner_id': 'QA_AUTOMATION',
    'cmi.learner_name': 'QA, Automation',
    'cmi.score.scaled': '', 'cmi.score.raw': '', 'cmi.score.min': '', 'cmi.score.max': '',
    'cmi._version': '1.0',
    'cmi.interactions._count': '0',
    'cmi.objectives._count': '0',
  };
  window.__cmi = cmi;
  const seed = window.__scormSeed || {};
  Object.assign(cmi, seed);
  let lastError = '0';
  function rec(fn, args, result) {
    log.push({ t: Date.now(), fn, args: Array.from(args), result });
    return result;
  }
  function countKey(prefix) {
    // maintain _count for collections (interactions, objectives)
    let n = 0;
    const seen = new Set();
    for (const k of Object.keys(cmi)) {
      const m = k.match(new RegExp('^' + prefix.replace(/\./g, '\\.') + '\\.(\\d+)\\.'));
      if (m) seen.add(+m[1]);
    }
    n = seen.size;
    cmi[prefix + '._count'] = String(n);
  }
  const api2004 = {
    Initialize: function (s) { lastError='0'; return rec('Initialize', arguments, 'true'); },
    Terminate: function (s) { lastError='0'; return rec('Terminate', arguments, 'true'); },
    GetValue: function (k) {
      lastError = '0';
      if (k in cmi) return rec('GetValue', arguments, cmi[k]);
      if (/\._count$/.test(k)) { countKey(k.replace(/\._count$/, '')); return rec('GetValue', arguments, cmi[k] || '0'); }
      if (/^cmi\.interactions\.\d+\./.test(k) || /^cmi\.objectives\.\d+\./.test(k)) { lastError='0'; return rec('GetValue', arguments, cmi[k] || ''); }
      lastError = '401';
      return rec('GetValue', arguments, '');
    },
    SetValue: function (k, v) {
      lastError = '0';
      cmi[k] = String(v);
      if (/^cmi\.(interactions|objectives)\.\d+\./.test(k)) countKey('cmi.' + k.split('.')[1]);
      return rec('SetValue', arguments, 'true');
    },
    Commit: function (s) { return rec('Commit', arguments, 'true'); },
    GetLastError: function () { return lastError; },
    GetErrorString: function (c) { return ''; },
    GetDiagnostic: function (c) { return ''; },
  };
  const api12 = {
    LMSInitialize: function () { return rec('LMSInitialize', arguments, 'true'); },
    LMSFinish: function () { return rec('LMSFinish', arguments, 'true'); },
    LMSGetValue: function (k) { return rec('LMSGetValue', arguments, cmi[k] || ''); },
    LMSSetValue: function (k, v) { cmi[k] = String(v); return rec('LMSSetValue', arguments, 'true'); },
    LMSCommit: function () { return rec('LMSCommit', arguments, 'true'); },
    LMSGetLastError: function () { return '0'; },
    LMSGetErrorString: function () { return ''; },
    LMSGetDiagnostic: function () { return ''; },
  };
  window.API_1484_11 = api2004;
  window.API = api12;
})();
