import { api } from "../api.js";
const { ref, onMounted } = Vue;
export const DashboardView = {
  setup() {
    const data = ref(null); const health = ref(null); const err = ref("");
    async function reload() {
      err.value = "";
      try {
        health.value = await api("/api/health");
        data.value = await api("/api/dashboard");
      } catch (e) { err.value = e.message || String(e); }
    }
    function fmtTime(t) {
      if (!t) return "-";
      try { return new Date(t * 1000).toLocaleTimeString(); } catch { return String(t); }
    }
    onMounted(reload);
    return { data, health, err, reload, fmtTime };
  },
  template: `
  <div class="grid">
    <div class="panel span-12">
      <h2>发布控制台</h2>
      <p class="muted">{{ health && health.skeleton }}</p>
      <div class="err" v-if="err">{{ err }}</div>
    </div>

    <div class="panel span-12" v-if="data && data.stub_panel">
      <h2>空桩工位锁 / 实推面板（账实口径，禁止假绿）</h2>
      <div style="display:flex;gap:.7rem;align-items:center;flex-wrap:wrap;margin:.4rem 0 .8rem">
        <span class="pill" :class="data.stub_panel.tone === 'success' ? '' : data.stub_panel.tone">
          {{ data.stub_panel.label }}
        </span>
        <span class="pill" :class="data.publish_lock_ok ? '' : 'danger'">
          锁面 {{ data.publish_lock_ok ? '正常' : '有未释放锁' }}
        </span>
        <span class="pill" :class="data.seat_lock_held ? 'danger' : ''">
          工位锁 {{ data.seat_lock_held ? '占用 · ' + (data.stub_seat_lock && data.stub_seat_lock.holder) : '空闲' }}
        </span>
        <span class="pill warn" v-if="data.real_push_queued">排队实推 {{ data.real_push_queued }}</span>
        <span class="pill" v-if="data.real_push_pushed">已实推 {{ data.real_push_pushed }}</span>
        <button class="btn secondary" @click="reload">刷新</button>
      </div>
      <p class="hint" v-if="data.real_push_queued">排队中的正式实推不会被计为成功；请先到「空桩」页核查工位锁泄漏并卸锁，再排空队列。</p>
      <table v-if="data.stub_real_push && data.stub_real_push.items && data.stub_real_push.items.length">
        <thead><tr><th>#</th><th>排队内容</th><th>发起人</th><th>时间</th></tr></thead>
        <tbody>
          <tr v-for="q in data.stub_real_push.items" :key="q.id">
            <td>{{ q.id }}</td><td>{{ q.note || '-' }}</td><td>{{ q.actor }}</td><td>{{ fmtTime(q.created_at) }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="panel span-4" v-if="data"><div class="muted">开关数</div><p class="stat">{{ data.flags }}</p></div>
    <div class="panel span-4" v-if="data"><div class="muted">已生效</div><p class="stat">{{ data.active }}</p></div>
    <div class="panel span-4" v-if="data"><div class="muted">开放影子</div><p class="stat">{{ data.open_shadows }}</p></div>
    <div class="panel span-6" v-if="data">
      <h2>状态分布</h2>
      <table><thead><tr><th>状态</th><th>数量</th></tr></thead>
      <tbody><tr v-for="s in data.by_status" :key="s.status"><td>{{ s.label }}</td><td>{{ s.c }}</td></tr></tbody></table>
    </div>
    <div class="panel span-6" v-if="data">
      <h2>最近变更</h2>
      <table><thead><tr><th>动作</th><th>详情</th><th>操作者</th></tr></thead>
      <tbody><tr v-for="h in data.recent" :key="h.id"><td>{{ h.action }}</td><td>{{ h.detail }}</td><td>{{ h.actor }}</td></tr></tbody></table>
      <button class="btn secondary" style="margin-top:0.7rem" @click="reload">刷新</button>
    </div>

    <div class="panel span-6" v-if="data">
      <h2>空桩争用痕（败方留痕）</h2>
      <table>
        <thead><tr><th>锁名</th><th>败方</th><th>胜方</th><th>时间</th></tr></thead>
        <tbody>
          <tr v-for="m in data.stub_contention" :key="m.id">
            <td><code>{{ m.lock_key }}</code></td><td>{{ m.loser }}</td><td>{{ m.winner }}</td><td>{{ fmtTime(m.created_at) }}</td>
          </tr>
          <tr v-if="!data.stub_contention || !data.stub_contention.length"><td colspan="4" class="matrix-empty">暂无争用</td></tr>
        </tbody>
      </table>
    </div>
    <div class="panel span-6" v-if="data">
      <h2>空桩卸锁核销（跨表账实）</h2>
      <table>
        <thead><tr><th>#</th><th>来源</th><th>锁主</th><th>时间</th></tr></thead>
        <tbody>
          <tr v-for="w in data.stub_writeoffs" :key="w.id">
            <td>{{ w.id }}</td><td>{{ w.source }}</td><td>{{ w.holder }}</td><td>{{ fmtTime(w.created_at) }}</td>
          </tr>
          <tr v-if="!data.stub_writeoffs || !data.stub_writeoffs.length"><td colspan="4" class="matrix-empty">暂无核销</td></tr>
        </tbody>
      </table>
    </div>
  </div>`
};
