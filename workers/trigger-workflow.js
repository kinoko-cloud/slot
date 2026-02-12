/**
 * Cloudflare Worker: GitHub Actionsワークフローをトリガー
 * Cron Trigger: 毎時15分に実行（GitHub Actionsの毎時0分と補完）
 */

const GITHUB_TOKEN = ''; // 環境変数から取得: GITHUB_PAT
const REPO_OWNER = 'kinoko-cloud';
const REPO_NAME = 'slot';

async function triggerWorkflow(workflow, token) {
  const url = `https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/actions/workflows/${workflow}/dispatches`;
  
  const response = await fetch(url, {
    method: 'POST',
    headers: {
      'Authorization': `token ${token}`,
      'Accept': 'application/vnd.github.v3+json',
      'User-Agent': 'Cloudflare-Worker',
    },
    body: JSON.stringify({ ref: 'main' }),
  });
  
  return response.status === 204;
}

async function checkDataFreshness() {
  const url = `https://raw.githubusercontent.com/${REPO_OWNER}/${REPO_NAME}/main/data/availability.json`;
  
  try {
    const response = await fetch(url);
    if (!response.ok) return null;
    
    const data = await response.json();
    const fetchedAt = data.fetched_at;
    if (!fetchedAt) return null;
    
    const fetchedTime = new Date(fetchedAt);
    const now = new Date();
    const ageMinutes = (now - fetchedTime) / (1000 * 60);
    
    return ageMinutes;
  } catch (e) {
    console.error('Error checking data:', e);
    return null;
  }
}

export default {
  async scheduled(event, env, ctx) {
    const token = env.GITHUB_PAT;
    if (!token) {
      console.error('GITHUB_PAT not set');
      return;
    }
    
    // JST時刻を取得
    const now = new Date();
    const jstHour = (now.getUTCHours() + 9) % 24;
    
    // 営業時間外（9時前、23時以降）はスキップ
    if (jstHour < 9 || jstHour >= 23) {
      console.log(`Outside business hours (${jstHour}:00 JST), skipping`);
      return;
    }
    
    // データの新鮮さをチェック
    const ageMinutes = await checkDataFreshness();
    console.log(`Data age: ${ageMinutes?.toFixed(0) || 'unknown'} minutes`);
    
    // 90分以上古い場合はFetch Availabilityをトリガー
    if (ageMinutes !== null && ageMinutes > 90) {
      console.log('Data is stale, triggering fetch...');
      const success = await triggerWorkflow('fetch-availability.yml', token);
      console.log(`Fetch trigger: ${success ? 'success' : 'failed'}`);
      return;
    }
    
    // Deploy Static Siteをトリガー
    console.log('Triggering deploy static...');
    const success = await triggerWorkflow('deploy-static.yml', token);
    console.log(`Deploy trigger: ${success ? 'success' : 'failed'}`);
  },
  
  async fetch(request, env, ctx) {
    return new Response('Slot Workflow Trigger Worker', { status: 200 });
  },
};
