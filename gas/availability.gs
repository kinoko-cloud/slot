/**
 * パチスロ空き状況取得 GAS
 * papimo.jpから空き/遊技中を取得してJSONで返す
 */

// 店舗設定
const STORES = {
  'island_akihabara_sbj': {
    hall_id: '00031715',
    machine_id: '225010000',
    name: 'アイランド秋葉原 SBJ'
  }
  // 他の店舗も追加可能
};

/**
 * papimo.jpから空き状況を取得
 */
function fetchPapimoAvailability(hallId, machineId) {
  const url = `https://papimo.jp/h/${hallId}/hit/index_sort/${machineId}/1-20-1274324`;

  try {
    const response = UrlFetchApp.fetch(url, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X)'
      },
      muteHttpExceptions: true
    });

    const html = response.getContentText();

    // 遊技中の台を取得
    const playingMatches = html.match(/<span class="badge-work">遊技中<\/span>(\d{4})/g) || [];
    const playing = playingMatches.map(m => m.match(/(\d{4})/)[1]);

    // 全台番号を取得
    const allMatches = html.match(/\/hit\/view\/(\d{4})/g) || [];
    const allUnits = [...new Set(allMatches.map(m => m.match(/(\d{4})/)[1]))];

    // 空き = 全台 - 遊技中
    const empty = allUnits.filter(u => !playing.includes(u));

    return {
      empty: empty.sort(),
      playing: playing.sort(),
      total: allUnits.length,
      fetched_at: new Date().toISOString()
    };
  } catch (e) {
    return { error: e.message };
  }
}

/**
 * 全店舗の空き状況を取得
 */
function getAllAvailability() {
  const result = {
    stores: {},
    fetched_at: new Date().toISOString()
  };

  for (const [storeKey, config] of Object.entries(STORES)) {
    const data = fetchPapimoAvailability(config.hall_id, config.machine_id);
    result.stores[storeKey] = {
      name: config.name,
      ...data
    };
  }

  return result;
}

/**
 * Webアプリとして公開するエンドポイント
 */
function doGet(e) {
  const storeKey = e?.parameter?.store;

  let data;
  if (storeKey && STORES[storeKey]) {
    const config = STORES[storeKey];
    data = {
      store: storeKey,
      name: config.name,
      ...fetchPapimoAvailability(config.hall_id, config.machine_id)
    };
  } else {
    data = getAllAvailability();
  }

  return ContentService
    .createTextOutput(JSON.stringify(data))
    .setMimeType(ContentService.MimeType.JSON);
}

/**
 * テスト用
 */
function test() {
  const result = getAllAvailability();
  Logger.log(JSON.stringify(result, null, 2));
}
