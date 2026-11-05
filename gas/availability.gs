/**
 * パチスロデータ取得 GAS
 * - papimo.jp: アイランド秋葉原の空き/遊技中
 * - daidata.goraggio.com: エスパス全店舗のBB/RB/ART/G数
 */

// papimo 店舗設定（アイランド秋葉原用）
const PAPIMO_STORES = {
  'island_akihabara_sbj': {
    hall_id: '00031715',
    machine_id: '225010000',
    name: 'アイランド秋葉原 SBJ'
  }
};

// daidata 店舗設定（エスパス全店舗・東京喰種のみ）
const DAIDATA_STORES = {
  'shinjuku_espass_tokyoghoul': {
    hall_id: '100949',
    model_encoded: 'L%E6%9D%B1%E4%BA%AC%E5%96%B0%E7%A8%AE',
    name: 'エスパス新宿 東京喰種'
  },
  'akiba_espass_tokyoghoul': {
    hall_id: '100928',
    model_encoded: 'L%E6%9D%B1%E4%BA%AC%E5%96%B0%E7%A8%AE',
    name: '秋葉原エスパス 東京喰種'
  },
  'seibu_shinjuku_espass_tokyoghoul': {
    hall_id: '100950',
    model_encoded: 'L%E6%9D%B1%E4%BA%AC%E5%96%B0%E7%A8%AE',
    name: '西武新宿エスパス 東京喰種'
  },
  'shibuya_espass_tokyoghoul': {
    hall_id: '100860',
    model_encoded: 'L%E6%9D%B1%E4%BA%AC%E5%96%B0%E7%A8%AE',
    name: '渋谷エスパス 東京喰種'
  }
};

// ----- Cookie utilities -----

function parseCookies(setCookieHeaders) {
  const cookies = {};
  const headers = Array.isArray(setCookieHeaders) ? setCookieHeaders : [setCookieHeaders];
  for (const header of headers) {
    if (!header) continue;
    const parts = header.split(';');
    const eqIdx = parts[0].indexOf('=');
    if (eqIdx < 0) continue;
    const name = parts[0].substring(0, eqIdx).trim();
    const value = parts[0].substring(eqIdx + 1).trim();
    if (name) cookies[name] = value;
  }
  return cookies;
}

function buildCookieHeader(cookies) {
  return Object.entries(cookies).map(([k, v]) => `${k}=${v}`).join('; ');
}

// ----- daidata: 規約同意フロー -----

const DAIDATA_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36';
const DAIDATA_BASE = 'https://daidata.goraggio.com';

/**
 * hallIdの利用規約に同意してセッションクッキーを返す
 * @returns {cookies: {...}, error: null|string}
 */
function acceptTermsForHall(hallId) {
  let cookies = {};

  // Step 1: GET unit_list (dummy model) → 302 → /accept
  const dummyUrl = `${DAIDATA_BASE}/${hallId}/accept`;
  try {
    const res1 = UrlFetchApp.fetch(dummyUrl, {
      headers: { 'User-Agent': DAIDATA_UA },
      muteHttpExceptions: true,
      followRedirects: false
    });
    const hdrs1 = res1.getAllHeaders();
    const sc1 = hdrs1['Set-Cookie'] || hdrs1['set-cookie'];
    if (sc1) Object.assign(cookies, parseCookies(sc1));
    // If already accepted (200), return immediately
    if (res1.getResponseCode() === 200) {
      const html1 = res1.getContentText();
      const csrfM = html1.match(/name="_token"[^>]*value="([^"]+)"/);
      if (csrfM) {
        return _doAcceptPost(hallId, cookies, csrfM[1], dummyUrl);
      }
    }
  } catch (e) {
    return { cookies, error: 'accept step1: ' + e.message };
  }

  // Step 2: GET /accept page
  let csrfToken = '';
  let acceptPostUrl = `${DAIDATA_BASE}/${hallId}/accept`;
  try {
    const res2 = UrlFetchApp.fetch(`${DAIDATA_BASE}/${hallId}/accept`, {
      headers: { 'User-Agent': DAIDATA_UA, 'Cookie': buildCookieHeader(cookies) },
      muteHttpExceptions: true,
      followRedirects: true
    });
    const hdrs2 = res2.getAllHeaders();
    const sc2 = hdrs2['Set-Cookie'] || hdrs2['set-cookie'];
    if (sc2) Object.assign(cookies, parseCookies(sc2));
    const html2 = res2.getContentText();
    const csrfMatch = html2.match(/name="_token"[^>]*value="([^"]+)"/);
    csrfToken = csrfMatch ? csrfMatch[1] : '';
    const actionMatch = html2.match(/<form[^>]*action="([^"]+)"/);
    if (actionMatch) {
      acceptPostUrl = actionMatch[1].startsWith('http') ? actionMatch[1] : `${DAIDATA_BASE}${actionMatch[1]}`;
    }
  } catch (e) {
    return { cookies, error: 'accept step2: ' + e.message };
  }

  return _doAcceptPost(hallId, cookies, csrfToken, acceptPostUrl);
}

function _doAcceptPost(hallId, cookies, csrfToken, acceptPostUrl) {
  try {
    const payload = csrfToken ? `_token=${encodeURIComponent(csrfToken)}&agree=1` : 'agree=1';
    const res3 = UrlFetchApp.fetch(acceptPostUrl, {
      method: 'post',
      headers: {
        'User-Agent': DAIDATA_UA,
        'Cookie': buildCookieHeader(cookies),
        'Content-Type': 'application/x-www-form-urlencoded',
        'Referer': acceptPostUrl,
        'X-XSRF-TOKEN': cookies['XSRF-TOKEN'] || '',
      },
      payload: payload,
      muteHttpExceptions: true,
      followRedirects: false
    });
    const hdrs3 = res3.getAllHeaders();
    const sc3 = hdrs3['Set-Cookie'] || hdrs3['set-cookie'];
    if (sc3) Object.assign(cookies, parseCookies(sc3));
    return { cookies, error: null };
  } catch (e) {
    return { cookies, error: 'accept POST: ' + e.message };
  }
}

/**
 * 既存セッションでunit_listを取得。/acceptにリダイレクトされたら再同意して再取得
 * @returns {html: string, error: null|string}
 */
function fetchUnitListHtml(hallId, modelEncoded, cookies) {
  const listUrl = `${DAIDATA_BASE}/${hallId}/unit_list?model=${modelEncoded}&ballPrice=21.70&ps=S`;
  try {
    const res = UrlFetchApp.fetch(listUrl, {
      headers: { 'User-Agent': DAIDATA_UA, 'Cookie': buildCookieHeader(cookies) },
      muteHttpExceptions: true,
      followRedirects: true
    });
    const code = res.getResponseCode();
    const html = res.getContentText();
    if (code === 404 || html.includes('機種が見つかりませんでした')) {
      return { html: null, error: '機種不明 (404)' };
    }
    if (!html.includes('<table')) {
      return { html: null, error: 'テーブルなし (おそらく規約未同意)' };
    }
    return { html, error: null };
  } catch (e) {
    return { html: null, error: e.message };
  }
}

// ----- daidata: HTMLパーサー -----

/**
 * unit_listページのHTMLをパースして台データ配列を返す
 * テーブル列順: status | 台番号 | 累計スタート | BB | RB | ART | 最大持ち玉 | BB確率 | RB確率 | ART確率 | 合成確率 | 前日最終スタート | スタート回数
 */
function parseDaidataUnitListHtml(html) {
  const units = [];

  // <tbody>内の<tr>を順にパース
  const tbodyMatch = html.match(/<tbody>([\s\S]*?)<\/tbody>/i);
  if (!tbodyMatch) return units;

  const trRegex = /<tr[^>]*>([\s\S]*?)<\/tr>/gi;
  let trMatch;

  while ((trMatch = trRegex.exec(tbodyMatch[1])) !== null) {
    const rowHtml = trMatch[1];

    // 遊技中 = icon-user が行内に存在
    const isPlaying = rowHtml.includes('icon-user');

    // 台番号: detail?unit=NNN から取得
    const unitMatch = rowHtml.match(/\/detail\?unit=(\d+)/);
    if (!unitMatch) continue;
    const unitId = unitMatch[1];

    // データ列: <td class="today">VALUE</td>
    // 順序: [0]累計スタート [1]BB [2]RB [3]ART [4]最大持ち玉
    //       [5]BB確率 [6]RB確率 [7]ART確率 [8]合成確率
    //       [9]前日最終スタート [10]スタート回数
    const todayTds = [];
    const tdRegex = /<td[^>]*class="today"[^>]*>([\s\S]*?)<\/td>/gi;
    let tdMatch;
    while ((tdMatch = tdRegex.exec(rowHtml)) !== null) {
      const text = tdMatch[1].replace(/<[^>]+>/g, '').replace(/&nbsp;/g, '').trim();
      todayTds.push(text);
    }

    if (todayTds.length < 4) continue;

    const toInt = (s) => { const n = parseInt((s || '0').replace(/,/g, ''), 10); return isNaN(n) ? 0 : n; };

    units.push({
      unit_id: unitId,
      playing: isPlaying,
      total_start: toInt(todayTds[0]),    // 累計スタート
      bb: toInt(todayTds[1]),             // BB回数
      rb: toInt(todayTds[2]),             // RB回数
      art: toInt(todayTds[3]),            // ART回数
      max_medals: toInt(todayTds[4]),     // 最大持ち玉
      // todayTds[5-8] = 確率 (fraction strings, skip)
      prev_final_start: toInt(todayTds[9]),  // 前日最終スタート
      final_start: toInt(todayTds[10]),      // スタート回数（現在のハマり）
    });
  }

  return units;
}

// ----- daidata: 全店舗スクレイプ -----

/**
 * 全エスパス店舗のdaidataを取得してJSONで返す
 * ホール別にセッションを共有して効率化
 */
function scrapeDaidataAllStores() {
  const result = {
    stores: {},
    fetched_at: new Date().toISOString(),
    errors: []
  };

  // ホール別にセッションを共有
  const hallSessions = {};  // hallId -> cookies

  for (const [storeKey, config] of Object.entries(DAIDATA_STORES)) {
    const { hall_id, model_encoded, name } = config;

    // セッションがなければ規約同意
    if (!hallSessions[hall_id]) {
      const acceptResult = acceptTermsForHall(hall_id);
      if (acceptResult.error) {
        result.errors.push(`${storeKey}: accept error: ${acceptResult.error}`);
      }
      hallSessions[hall_id] = acceptResult.cookies;
    }

    // unit_listを取得
    const fetchResult = fetchUnitListHtml(hall_id, model_encoded, hallSessions[hall_id]);
    if (fetchResult.error) {
      result.errors.push(`${storeKey}: fetch error: ${fetchResult.error}`);
      result.stores[storeKey] = { name, error: fetchResult.error, units: [] };
      continue;
    }

    const units = parseDaidataUnitListHtml(fetchResult.html);
    result.stores[storeKey] = {
      name,
      units,
      total: units.length,
      playing_count: units.filter(u => u.playing).length
    };
  }

  return result;
}

// ----- papimo: 空き状況 -----

function fetchPapimoAvailability(hallId, machineId) {
  const url = `https://papimo.jp/h/${hallId}/hit/index_sort/${machineId}/1-20-1274324`;
  try {
    const response = UrlFetchApp.fetch(url, {
      headers: { 'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X)' },
      muteHttpExceptions: true
    });
    const html = response.getContentText();
    const playingMatches = html.match(/<span class="badge-work">遊技中<\/span>(\d{4})/g) || [];
    const playing = playingMatches.map(m => m.match(/(\d{4})/)[1]);
    const allMatches = html.match(/\/hit\/view\/(\d{4})/g) || [];
    const allUnits = [...new Set(allMatches.map(m => m.match(/(\d{4})/)[1]))];
    const empty = allUnits.filter(u => !playing.includes(u));
    return { empty: empty.sort(), playing: playing.sort(), total: allUnits.length, fetched_at: new Date().toISOString() };
  } catch (e) {
    return { error: e.message };
  }
}

// ----- Web App エンドポイント -----

function doGet(e) {
  const action = e?.parameter?.action;

  let data;

  if (action === 'scrape_daidata') {
    // 全エスパス店舗のdaidataを返す（GitHub Actions用）
    data = scrapeDaidataAllStores();

  } else if (action === 'scrape_store') {
    // 特定店舗のdaidataを返す（テスト用）
    const hallId = e?.parameter?.hall_id || '100949';
    const modelEncoded = e?.parameter?.model || DAIDATA_STORES['shinjuku_espass_tokyoghoul'].model_encoded;
    const accept = acceptTermsForHall(hallId);
    const fetched = fetchUnitListHtml(hallId, modelEncoded, accept.cookies);
    if (fetched.error) {
      data = { error: fetched.error };
    } else {
      data = {
        units: parseDaidataUnitListHtml(fetched.html),
        fetched_at: new Date().toISOString()
      };
    }

  } else if (action === 'test_daidata') {
    data = testDaidataAccess();

  } else if (action === 'debug_html') {
    const hallId = e?.parameter?.hall_id || '100949';
    const modelEncoded = e?.parameter?.model || DAIDATA_STORES['shinjuku_espass_tokyoghoul'].model_encoded;
    const accept = acceptTermsForHall(hallId);
    const fetched = fetchUnitListHtml(hallId, modelEncoded, accept.cookies);
    const html = fetched.html || '';
    const tableStart = html.indexOf('<table');
    const tableEnd = html.indexOf('</table>');
    data = {
      table_html: tableStart >= 0 ? html.substring(tableStart, tableEnd + 8).substring(0, 4000) : 'NO TABLE',
      html_length: html.length,
      error: fetched.error
    };

  } else {
    // デフォルト: papimo空き状況
    const storeKey = e?.parameter?.store;
    if (storeKey && PAPIMO_STORES[storeKey]) {
      const config = PAPIMO_STORES[storeKey];
      data = { store: storeKey, name: config.name, ...fetchPapimoAvailability(config.hall_id, config.machine_id) };
    } else {
      const result = { stores: {}, fetched_at: new Date().toISOString() };
      for (const [sk, cfg] of Object.entries(PAPIMO_STORES)) {
        result.stores[sk] = { name: cfg.name, ...fetchPapimoAvailability(cfg.hall_id, cfg.machine_id) };
      }
      data = result;
    }
  }

  return ContentService
    .createTextOutput(JSON.stringify(data))
    .setMimeType(ContentService.MimeType.JSON);
}

// ----- テスト用関数 -----

function testDaidataAccess() {
  const TARGET = `${DAIDATA_BASE}/100949/unit_list?model=${DAIDATA_STORES['shinjuku_espass_tokyoghoul'].model_encoded}&ballPrice=21.70&ps=S`;
  let myIp = '?';
  try {
    const ipRes = UrlFetchApp.fetch('https://ipinfo.io/json', { headers: { 'User-Agent': DAIDATA_UA }, muteHttpExceptions: true });
    const ipData = JSON.parse(ipRes.getContentText());
    myIp = `${ipData.ip} / ${ipData.org} / ${ipData.country}`;
  } catch (e) { myIp = 'failed: ' + e.message; }

  let statusCode, responseHeaders;
  try {
    const res = UrlFetchApp.fetch(TARGET, { headers: { 'User-Agent': DAIDATA_UA }, muteHttpExceptions: true, followRedirects: false });
    statusCode = res.getResponseCode();
    responseHeaders = res.getAllHeaders();
  } catch (e) { statusCode = 0; responseHeaders = {}; }

  return {
    gas_ip: myIp,
    daidata_status: statusCode,
    success: statusCode === 302 || statusCode === 200,
    location_header: responseHeaders['Location'] || responseHeaders['location'] || null,
    tested_at: new Date().toISOString()
  };
}

function testScrapeDaidata() {
  const result = scrapeDaidataAllStores();
  Logger.log(JSON.stringify(result, null, 2));
}

function testScrapeOneStore() {
  const config = DAIDATA_STORES['shinjuku_espass_tokyoghoul'];
  const accept = acceptTermsForHall(config.hall_id);
  Logger.log('accept cookies: ' + JSON.stringify(Object.keys(accept.cookies)));
  const fetched = fetchUnitListHtml(config.hall_id, config.model_encoded, accept.cookies);
  if (fetched.error) { Logger.log('error: ' + fetched.error); return; }
  const units = parseDaidataUnitListHtml(fetched.html);
  Logger.log('units count: ' + units.length);
  Logger.log(JSON.stringify(units.slice(0, 3), null, 2));
}
