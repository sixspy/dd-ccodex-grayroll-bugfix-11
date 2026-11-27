import { api } from "../api.js";
const { ref, onMounted } = Vue;

export const StubsView = {
  setup() {
    const remote = ref(null);
    const exp = ref(null);
    const err = ref("");
    const msg = ref("");
    const busy = ref(false);

    // BUGFIX-11：工位锁 / 演练 / 实推队列 / 争用核销分账
    const seat = ref(null);
    const drills = ref([]);
    const drafts = ref([]);
    const pushes = ref([]);
    const drillNote = ref("");
    const pushNote = ref("");
    const lastDrill = ref(null);

    function pretty(v) {
      return JSON.stringify(v, null, 2);
    }
    function fmtTime(t) {
      if (!t) return "-";
      try { return new Date(t * 1000).toLocaleTimeString(); } catch { return String(t); }
    }

    async function reload() {
      err.value = "";
      try {
        const [r1, r2, r3, r4] = await Promise.all([
          api("/api/stubs/remote-push"),
          api("/api/stubs/experiment-bridge"),
          api("/api/stubs/seat-lock"),
          api("/api/stubs/drills"),
        ]);
        remote.value = r1;
        exp.value = r2;
        seat.value = r3;
        drills.value = r4.items || [];
        drafts.value = r4.drafts || [];
        const p = await api("/api/stubs/real-push");
        pushes.value = p.items || [];
      } catch (e) {
        err.value = e.message || String(e);
      }
    }

    async function runAction(fn) {
      err.value = ""; msg.value = ""; busy.value = true;
      try { await fn(); await reload(); }
      catch (e) { err.value = e.message || String(e); await reload(); }
      finally { busy.value = false; }
    }

    function acquire() {
      return runAction(async () => {
        await api("/api/stubs/seat-lock/acquire", { method: "POST", body: "{}" });
        msg.value = "工位锁已占用";
      });
    }
    function release() {
      return runAction(async () => {
        await api("/api/stubs/seat-lock/release", { method: "POST", body: "{}" });
        msg.value = "工位锁已卸载";
      });
    }
    function submitDrill(useShadow) {
      return runAction(async () => {
        const body = JSON.stringify({ note: drillNote.value || "页面演练", use_shadow: !!useShadow });
        const res = await api("/api/stubs/drills", { method: "POST", body });
        lastDrill.value = res;
        msg.value = useShadow
          ? `已落影子卸锁草案 #${res.draft_id}，正式工位锁零改`
          : `演练 #${res.drill_id} 成功，工位锁已同事务卸载并核销`;
        drillNote.value = "";
      });
    }
    function applyDraft(id) {
      return runAction(async () => {
        await api(`/api/stubs/unlock-drafts/${id}/apply`, { method: "POST", body: "{}" });
        msg.value = `影子草案 #${id} 已原子升正卸锁`;
      });
    }
    function pushReal() {
      return runAction(async () => {
        const res = await api("/api/stubs/real-push", {
          method: "POST",
          body: JSON.stringify({ note: pushNote.value || "页面实推" }),
        });
        msg.value = res.status === "queued" ? "工位锁被占，实推已如实排队（非成功）" : "实推完成";
        pushNote.value = "";
      });
    }
    function drain() {
      return runAction(async () => {
        const res = await api("/api/stubs/real-pushes/drain", { method: "POST", body: "{}" });
        msg.value = `已排空 ${res.pushed || 0} 条排队实推`;
      });
    }

    // 原题面 501 空桩
    async function touchRemote() {
      err.value = "";
      try { await api("/api/stubs/remote-push/run", { method: "POST", body: "{}" }); }
      catch (e) { err.value = e.message || String(e); }
    }
    async function touchExp() {
      err.value = "";
      try { await api("/api/stubs/experiment-bridge/link", { method: "POST", body: "{}" }); }
      catch (e) { err.value = e.message || String(e); }
    }

    onMounted(reload);
    return {
      remote, exp, err, msg, busy, seat, drills, drafts, pushes,
      drillNote, pushNote, lastDrill,
      pretty, fmtTime, reload, acquire, release,
      submitDrill, applyDraft, pushReal, drain, touchRemote, touchExp,
    };
  },
  template: `
  <div class="grid">
    <div class="panel span-12">
      <h2>空桩演练 · 工位锁「空桩工位锁互斥」</h2>
      <p class="muted">甲路径：演练提交与卸锁同事务，跨表联查核销，失败正式零增回滚；乙路径：先落影子卸锁草案（正式锁零改），再原子升正卸锁。两路抢锁败方 409 且只留争用痕。</p>
      <div class="err" v-if="err">{{ err }}</div>
      <p class="hint" v-if="msg" style="border-left-color:var(--warn)">{{ msg }}</p>

      <div style="display:flex;gap:.6rem;flex-wrap:wrap;align-items:center;margin:.6rem 0">
        <span class="pill danger" v-if="seat && seat.held">工位锁占用 · {{ seat.holder }}</span>
        <span class="pill" v-else>工位锁空闲</span>
        <span class="pill warn" v-if="seat && seat.queued_real_pushes">排队实推 {{ seat.queued_real_pushes }}</span>
        <button class="btn" :disabled="busy" @click="acquire">占锁</button>
        <button class="btn secondary" :disabled="busy" @click="release">卸锁</button>
        <button class="btn secondary" :disabled="busy" @click="reload">刷新</button>
      </div>

      <div class="grid">
        <div class="panel span-6" style="background:rgba(255,255,255,.02)">
          <h3>演练提交</h3>
          <div class="field"><label>演练说明</label><input v-model="drillNote" placeholder="例如：空桩全链路演练-11" /></div>
          <button class="btn" :disabled="busy" @click="submitDrill(false)">甲路径：正式演练并同事务卸锁</button>
          <button class="btn secondary" :disabled="busy" style="margin-left:.5rem" @click="submitDrill(true)">乙路径：影子卸锁草案</button>
          <pre v-if="lastDrill" style="margin-top:.7rem">{{ pretty(lastDrill) }}</pre>

          <h3 style="margin-top:1rem">影子卸锁草案（待原子升正）</h3>
          <table>
            <thead><tr><th>#</th><th>锁主</th><th>状态</th><th>操作</th></tr></thead>
            <tbody>
              <tr v-for="d in drafts" :key="d.id">
                <td>{{ d.id }}</td><td>{{ d.holder }}</td>
                <td><span class="pill" :class="d.status==='open' ? 'warn' : ''">{{ d.status }}</span></td>
                <td><button class="btn secondary" :disabled="busy || d.status!=='open'" @click="applyDraft(d.id)">原子升正卸锁</button></td>
              </tr>
              <tr v-if="!drafts.length"><td colspan="4" class="matrix-empty">暂无草案</td></tr>
            </tbody>
          </table>
        </div>

        <div class="panel span-6" style="background:rgba(255,255,255,.02)">
          <h3>正式实推（工位锁被占时如实排队）</h3>
          <div class="field"><label>实推内容</label><input v-model="pushNote" placeholder="例如：正式配置推送 prod" /></div>
          <button class="btn" :disabled="busy" @click="pushReal">发起实推</button>
          <button class="btn secondary" :disabled="busy" style="margin-left:.5rem" @click="drain">排空队列</button>
          <table style="margin-top:.7rem">
            <thead><tr><th>#</th><th>内容</th><th>状态</th><th>发起人</th><th>时间</th></tr></thead>
            <tbody>
              <tr v-for="p in pushes" :key="p.id">
                <td>{{ p.id }}</td><td>{{ p.note || '-' }}</td>
                <td><span class="pill" :class="p.status==='queued' ? 'warn' : ''">{{ p.status === 'queued' ? '排队中' : '已实推' }}</span></td>
                <td>{{ p.actor }}</td><td>{{ fmtTime(p.created_at) }}</td>
              </tr>
              <tr v-if="!pushes.length"><td colspan="5" class="matrix-empty">暂无实推</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <div class="panel span-6">
      <h2>争用痕（败方只留痕）</h2>
      <table>
        <thead><tr><th>锁名</th><th>败方</th><th>胜方</th><th>时间</th></tr></thead>
        <tbody>
          <tr v-for="m in (seat ? seat.contention : [])" :key="m.id">
            <td><code>{{ m.lock_key }}</code></td><td>{{ m.loser }}</td><td>{{ m.winner }}</td><td>{{ fmtTime(m.created_at) }}</td>
          </tr>
          <tr v-if="seat && !seat.contention.length"><td colspan="4" class="matrix-empty">暂无争用</td></tr>
        </tbody>
      </table>
    </div>
    <div class="panel span-6">
      <h2>卸锁核销账（跨表账实）</h2>
      <table>
        <thead><tr><th>#</th><th>来源</th><th>锁主</th><th>匹配</th><th>时间</th></tr></thead>
        <tbody>
          <tr v-for="w in (seat ? seat.writeoffs : [])" :key="w.id">
            <td>{{ w.id }}</td><td>{{ w.source }}</td><td>{{ w.holder }}</td>
            <td><span class="pill" :class="w.matched ? '' : 'danger'">{{ w.matched ? '账实相符' : '不符' }}</span></td>
            <td>{{ fmtTime(w.created_at) }}</td>
          </tr>
          <tr v-if="seat && !seat.writeoffs.length"><td colspan="5" class="matrix-empty">暂无核销</td></tr>
        </tbody>
      </table>
    </div>

    <div class="panel span-6">
      <h2>远程配置推送空桩</h2>
      <p class="muted">题面空桩；原接口契约保持 501 不动。</p>
      <pre>{{ pretty(remote) }}</pre>
      <button class="btn secondary" @click="touchRemote">尝试推送</button>
    </div>
    <div class="panel span-6">
      <h2>实验平台桥接空桩</h2>
      <p class="muted">题面空桩；原接口契约保持 501 不动。</p>
      <pre>{{ pretty(exp) }}</pre>
      <button class="btn secondary" @click="touchExp">尝试桥接</button>
    </div>

    <div class="panel span-12">
      <h2>演练正式账</h2>
      <table>
        <thead><tr><th>#</th><th>路径</th><th>说明</th><th>状态</th><th>演练人</th><th>时间</th></tr></thead>
        <tbody>
          <tr v-for="d in drills" :key="d.id">
            <td>{{ d.id }}</td><td>{{ d.path }}</td><td>{{ d.note || '-' }}</td>
            <td><span class="pill">{{ d.status }}</span></td><td>{{ d.actor }}</td><td>{{ fmtTime(d.created_at) }}</td>
          </tr>
          <tr v-if="!drills.length"><td colspan="6" class="matrix-empty">暂无演练</td></tr>
        </tbody>
      </table>
    </div>
  </div>`
};
