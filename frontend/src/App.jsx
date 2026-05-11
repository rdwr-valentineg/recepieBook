import React, { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import {
  Search, Plus, X, Share2, Edit2, Trash2, Copy, Printer, ExternalLink,
  ChefHat, ArrowLeft, FileDown, ImagePlus, Check, Loader2, Sparkles,
  Camera, FileText, BookOpen, RefreshCw, Eye, Link2, AlertCircle, LogOut, Lock
} from 'lucide-react';
import { api, ApiError } from './api.js';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const getDomain = (url) => {
  if (!url) return '';
  try {
    return new URL(url).hostname.replace(/^www\./, '').replace(/^mobile\./, '');
  } catch { return ''; }
};

const formatDate = (iso) => {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleDateString('he-IL', { day: 'numeric', month: 'short', year: 'numeric' });
  } catch { return iso; }
};

const formatForShare = (recipe, category, shareUrl) => {
  const lines = [];
  lines.push(`${category?.emoji || '🍽️'} *${recipe.title}*`);
  if (category) lines.push(`קטגוריה: ${category.label}`);
  if (recipe.added_by) lines.push(`נוסף ע"י: ${recipe.added_by}`);
  lines.push('');
  if (recipe.ingredients?.trim()) {
    lines.push('📝 *רכיבים:*');
    lines.push(recipe.ingredients.trim());
    lines.push('');
  }
  if (recipe.instructions?.trim()) {
    lines.push('👨‍🍳 *הוראות הכנה:*');
    lines.push(recipe.instructions.trim());
    lines.push('');
  }
  if (recipe.notes?.trim()) {
    lines.push('📌 *הערות:*');
    lines.push(recipe.notes.trim());
    lines.push('');
  }
  if (shareUrl) {
    lines.push(`🔗 ${shareUrl}`);
    lines.push('');
  }
  lines.push('— מספר המתכונים שלנו ✨');
  return lines.join('\n');
};

// ---------------------------------------------------------------------------
// Root component: auth gate + main app
// ---------------------------------------------------------------------------

export default function App() {
  const [authState, setAuthState] = useState('loading'); // loading | out | in

  useEffect(() => {
    api.authStatus()
      .then(r => setAuthState(r.authenticated ? 'in' : 'out'))
      .catch(() => setAuthState('out'));
  }, []);

  if (authState === 'loading') {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <Loader2 className="animate-spin text-terracotta" size={32} />
      </div>
    );
  }

  if (authState === 'out') {
    return <Login onSuccess={() => setAuthState('in')} />;
  }

  return <RecipeApp onLogout={() => setAuthState('out')} />;
}

// ---------------------------------------------------------------------------
// Login
// ---------------------------------------------------------------------------

function Login({ onSuccess }) {
  const [pw, setPw] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setErr('');
    setBusy(true);
    try {
      await api.login(pw);
      onSuccess();
    } catch (e) {
      setErr(e.message || 'שגיאה');
    }
    setBusy(false);
  };

  return (
    <div dir="rtl" className="min-h-screen paper-bg flex items-center justify-center p-6">
      <div className="w-full max-w-sm bg-white rounded-3xl card-shadow p-8 fade-in">
        <div className="text-center mb-6">
          <div className="w-14 h-14 rounded-full bg-terracotta text-white flex items-center justify-center mx-auto mb-3">
            <ChefHat size={28} />
          </div>
          <h1 className="font-display text-2xl font-bold">מתכונים וטעימים</h1>
          <p className="text-sm text-ink/60 mt-1">ספר המתכונים המשפחתי</p>
        </div>
        <form onSubmit={submit} className="space-y-3">
          <div className="relative">
            <Lock size={16} className="absolute right-3 top-1/2 -translate-y-1/2 text-ink/40" />
            <input
              type="password"
              value={pw}
              onChange={e => setPw(e.target.value)}
              placeholder="סיסמה"
              autoFocus
              className="w-full pr-10 pl-3 py-2.5 bg-cream border border-ink/10 rounded-xl focus:outline-none focus:border-terracotta focus:ring-2 focus:ring-terracotta/15"
            />
          </div>
          {err && (
            <div className="text-sm text-red-700 bg-red-50 px-3 py-2 rounded-lg">{err}</div>
          )}
          <button
            type="submit"
            disabled={busy || !pw}
            className="w-full bg-terracotta hover:bg-terracotta-dark disabled:opacity-50 disabled:cursor-not-allowed text-white py-2.5 rounded-xl font-medium transition flex items-center justify-center gap-2"
          >
            {busy ? <Loader2 size={16} className="animate-spin" /> : null}
            כניסה
          </button>
        </form>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Recipe App
// ---------------------------------------------------------------------------

function RecipeApp({ onLogout }) {
  const [recipes, setRecipes] = useState([]);
  const [categories, setCategories] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [search, setSearch] = useState('');
  const [activeCat, setActiveCat] = useState('all');
  const [selectedRecipe, setSelectedRecipe] = useState(null);
  const [showAddSheet, setShowAddSheet] = useState(false);
  const [extractedDraft, setExtractedDraft] = useState(null); // {captureSessionId, extracted, capture, sourceUrl}
  const [editingRecipe, setEditingRecipe] = useState(null);
  const [toast, setToast] = useState(null);

  const showToast = useCallback((msg) => {
    setToast(msg);
    setTimeout(() => setToast(null), 2500);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const data = await api.listRecipes();
      setRecipes(data);
    } catch (e) {
      setError(e.message);
    }
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const [cats, recs] = await Promise.all([
          api.categories(),
          api.listRecipes(),
        ]);
        setCategories(cats);
        setRecipes(recs);
      } catch (e) {
        setError(e.message || 'שגיאה בטעינה');
      }
      setLoading(false);
    })();
  }, []);

  const getCat = (id) => categories.find(c => c.id === id);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return recipes
      .filter(r => activeCat === 'all' || r.category === activeCat)
      .filter(r => {
        if (!q) return true;
        return (
          r.title?.toLowerCase().includes(q) ||
          r.notes?.toLowerCase().includes(q) ||
          r.ingredients?.toLowerCase().includes(q) ||
          r.instructions?.toLowerCase().includes(q) ||
          r.added_by?.toLowerCase().includes(q)
        );
      });
  }, [recipes, search, activeCat]);

  const categoryCounts = useMemo(() => {
    const c = { all: recipes.length };
    for (const cat of categories) c[cat.id] = 0;
    for (const r of recipes) if (c[r.category] !== undefined) c[r.category]++;
    return c;
  }, [recipes, categories]);

  const handleLogout = async () => {
    try { await api.logout(); } catch (_) {}
    onLogout();
  };

  const onSaveNew = async (form) => {
    try {
      const created = await api.createRecipe({
        ...form,
        capture_session_id: extractedDraft?.captureSessionId,
      });
      setRecipes(prev => [created, ...prev]);
      setShowAddSheet(false);
      setExtractedDraft(null);
      showToast('המתכון נשמר ✓');
    } catch (e) {
      showToast(`שגיאה: ${e.message}`);
    }
  };

  const onSaveEdit = async (form) => {
    try {
      const updated = await api.updateRecipe(editingRecipe.id, form);
      setRecipes(prev => prev.map(r => r.id === updated.id ? updated : r));
      setEditingRecipe(null);
      setSelectedRecipe(updated);
      showToast('המתכון עודכן ✓');
    } catch (e) {
      showToast(`שגיאה: ${e.message}`);
    }
  };

  const onDelete = async (id) => {
    if (!confirm('למחוק את המתכון? אי אפשר לבטל.')) return;
    try {
      await api.deleteRecipe(id);
      setRecipes(prev => prev.filter(r => r.id !== id));
      setSelectedRecipe(null);
      showToast('המתכון נמחק');
    } catch (e) {
      showToast(`שגיאה: ${e.message}`);
    }
  };

  return (
    <div dir="rtl" className="min-h-screen paper-bg text-ink">
      {/* Header */}
      <header className="border-b border-ink/10 bg-cream/95 backdrop-blur sticky top-0 z-30 no-print">
        <div className="max-w-6xl mx-auto px-4 sm:px-6 py-4 flex items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-full bg-terracotta flex items-center justify-center text-white shrink-0">
              <ChefHat size={20} />
            </div>
            <div className="min-w-0">
              <h1 className="font-display text-xl sm:text-2xl font-bold leading-none truncate">מתכונים וטעימים</h1>
              <p className="text-xs text-ink/60 mt-0.5">ספר המתכונים שלנו 🥘</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setShowAddSheet(true)}
              className="flex items-center gap-1.5 bg-terracotta hover:bg-terracotta-dark text-white px-3 sm:px-4 py-2 rounded-full text-sm font-medium transition"
            >
              <Plus size={18} />
              <span className="hidden sm:inline">הוספת מתכון</span>
              <span className="sm:hidden">חדש</span>
            </button>
            <button
              onClick={handleLogout}
              title="התנתקות"
              className="p-2 rounded-full text-ink/60 hover:bg-ink/5 hover:text-ink transition"
            >
              <LogOut size={18} />
            </button>
          </div>
        </div>
      </header>

      {/* Search + chips */}
      <div className="max-w-6xl mx-auto px-4 sm:px-6 pt-5 pb-3 no-print">
        <div className="relative">
          <Search size={18} className="absolute right-3 top-1/2 -translate-y-1/2 text-ink/40" />
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="חיפוש — שם, רכיב, הוראה..."
            className="w-full pr-10 pl-10 py-3 bg-white border border-ink/10 rounded-2xl text-[15px] focus:outline-none focus:border-terracotta focus:ring-2 focus:ring-terracotta/15 transition"
          />
          {search && (
            <button onClick={() => setSearch('')} className="absolute left-3 top-1/2 -translate-y-1/2 text-ink/40 hover:text-ink">
              <X size={16} />
            </button>
          )}
        </div>

        <div className="flex gap-2 overflow-x-auto scrollbar-thin pb-1 mt-4 -mx-1 px-1">
          <CatChip active={activeCat === 'all'} onClick={() => setActiveCat('all')} emoji="📚" label="הכל" count={categoryCounts.all} />
          {categories.map(c => (
            categoryCounts[c.id] > 0 && (
              <CatChip key={c.id} active={activeCat === c.id} onClick={() => setActiveCat(c.id)} emoji={c.emoji} label={c.label} count={categoryCounts[c.id]} />
            )
          ))}
        </div>
      </div>

      {/* Main */}
      <main className="max-w-6xl mx-auto px-4 sm:px-6 pb-24">
        {loading ? (
          <div className="flex items-center justify-center py-24">
            <Loader2 className="animate-spin text-terracotta" size={32} />
          </div>
        ) : error ? (
          <div className="p-4 bg-red-50 border border-red-200 rounded-xl text-red-900 text-sm">
            ⚠️ {error}
          </div>
        ) : filtered.length === 0 ? (
          <EmptyState search={search} activeCat={activeCat} onAdd={() => setShowAddSheet(true)} />
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 sm:gap-5 mt-2">
            {filtered.map(r => (
              <RecipeCard key={r.id} recipe={r} category={getCat(r.category)} onClick={() => setSelectedRecipe(r)} />
            ))}
          </div>
        )}
      </main>

      {/* Detail modal */}
      {selectedRecipe && (
        <RecipeDetail
          recipe={selectedRecipe}
          category={getCat(selectedRecipe.category)}
          onClose={() => setSelectedRecipe(null)}
          onEdit={() => { setEditingRecipe(selectedRecipe); setSelectedRecipe(null); }}
          onDelete={onDelete}
          onUpdate={(r) => {
            setRecipes(prev => prev.map(x => x.id === r.id ? r : x));
            setSelectedRecipe(r);
          }}
          onShare={(text) => {
            navigator.clipboard.writeText(text).then(() => showToast('הועתק! 📋'));
          }}
          showToast={showToast}
        />
      )}

      {/* Add sheet */}
      {showAddSheet && !extractedDraft && (
        <AddSheet
          providers={[]}
          categories={categories}
          onClose={() => setShowAddSheet(false)}
          onExtracted={(data) => setExtractedDraft(data)}
          onSaveManual={onSaveNew}
        />
      )}

      {/* After extraction: review and save */}
      {extractedDraft && (
        <ReviewAndSave
          draft={extractedDraft}
          categories={categories}
          onCancel={() => { setExtractedDraft(null); setShowAddSheet(false); }}
          onBack={() => setExtractedDraft(null)}
          onSave={onSaveNew}
        />
      )}

      {/* Edit existing */}
      {editingRecipe && (
        <RecipeForm
          initial={editingRecipe}
          categories={categories}
          title="עריכת מתכון"
          onCancel={() => setEditingRecipe(null)}
          onSave={onSaveEdit}
        />
      )}

      {/* Toast */}
      {toast && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 bg-ink text-white px-5 py-3 rounded-full text-sm slide-up shadow-xl">
          {toast}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Small components
// ---------------------------------------------------------------------------

function CatChip({ active, onClick, emoji, label, count }) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-1.5 px-3.5 py-1.5 rounded-full text-sm whitespace-nowrap transition ${
        active ? 'bg-ink text-cream font-medium' : 'bg-white border border-ink/10 text-ink/80 hover:border-ink/30'
      }`}
    >
      <span>{emoji}</span>
      <span>{label}</span>
      {count !== undefined && (
        <span className={`text-xs ${active ? 'text-cream/60' : 'text-ink/40'}`}>{count}</span>
      )}
    </button>
  );
}

function RecipeCard({ recipe, category, onClick }) {
  const domain = getDomain(recipe.url);
  // Prefer user-uploaded image, then captured screenshot
  const thumbUrl = recipe.image_url || recipe.screenshot_url;
  return (
    <button
      onClick={onClick}
      className="text-right bg-white rounded-2xl overflow-hidden card-shadow hover:-translate-y-0.5 fade-in border border-ink/[0.04] group"
    >
      <div className="aspect-[5/3] relative overflow-hidden bg-gradient-to-br from-[#F3E9D7] to-[#E8D9B8]">
        {thumbUrl ? (
          <img src={thumbUrl} alt={recipe.title} loading="lazy"
               className={`w-full h-full ${recipe.image_url ? 'object-cover' : 'object-cover object-top'}`} />
        ) : (
          <div className="absolute inset-0 flex items-center justify-center">
            <span className="text-7xl opacity-50">{category?.emoji || '🍽️'}</span>
          </div>
        )}
        <div className="absolute top-3 right-3">
          <span className="inline-flex items-center gap-1 bg-white/90 backdrop-blur px-2.5 py-1 rounded-full text-xs font-medium text-ink">
            <span>{category?.emoji}</span>
            <span>{category?.label || 'שונות'}</span>
          </span>
        </div>
        {(recipe.has_pdf || recipe.has_screenshot) && (
          <div className="absolute bottom-3 right-3 flex gap-1">
            {recipe.has_pdf && (
              <span className="bg-white/85 backdrop-blur p-1 rounded-md" title="PDF נשמר">
                <FileText size={13} className="text-ink/70" />
              </span>
            )}
            {recipe.has_screenshot && !recipe.image_url && (
              <span className="bg-white/85 backdrop-blur p-1 rounded-md" title="צילום מסך נשמר">
                <Camera size={13} className="text-ink/70" />
              </span>
            )}
          </div>
        )}
      </div>
      <div className="p-4">
        <h3 className="font-display font-bold text-lg leading-tight mb-1 line-clamp-2 group-hover:text-terracotta transition-colors">{recipe.title}</h3>
        <div className="flex items-center justify-between text-xs text-ink/55 mt-2">
          <span>{recipe.added_by}</span>
          {domain && <span className="truncate max-w-[60%]">{domain}</span>}
        </div>
      </div>
    </button>
  );
}

function EmptyState({ search, activeCat, onAdd }) {
  return (
    <div className="text-center py-20 fade-in">
      <div className="text-6xl mb-4">{search || activeCat !== 'all' ? '🔍' : '📖'}</div>
      <h2 className="font-display text-2xl font-bold mb-2">
        {search ? 'לא נמצאו מתכונים' : activeCat !== 'all' ? 'אין מתכונים בקטגוריה הזו' : 'הספר עוד ריק'}
      </h2>
      <p className="text-ink/60 mb-6">
        {search ? `לא נמצאו תוצאות עבור "${search}"` : 'הוסיפו מתכון ראשון כדי להתחיל'}
      </p>
      {!search && (
        <button onClick={onAdd} className="inline-flex items-center gap-2 bg-terracotta hover:bg-terracotta-dark text-white px-5 py-3 rounded-full font-medium transition">
          <Plus size={18} />
          הוספת מתכון
        </button>
      )}
    </div>
  );
}

function IconBtn({ icon, label, onClick, danger, busy }) {
  return (
    <button
      onClick={onClick}
      disabled={busy}
      title={label}
      className={`p-2 rounded-full transition disabled:opacity-50 ${danger ? 'hover:bg-red-50 hover:text-red-600 text-ink/60' : 'hover:bg-ink/[0.06] text-ink/70'}`}
    >
      {busy ? <Loader2 size={17} className="animate-spin" /> : icon}
    </button>
  );
}

function Field({ label, hint, required, children }) {
  return (
    <div>
      <label className="block text-sm font-medium mb-1.5">
        {label}
        {required && <span className="text-terracotta mr-1">*</span>}
      </label>
      {children}
      {hint && <p className="text-xs text-ink/50 mt-1.5">{hint}</p>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// AddSheet: choose URL flow or manual flow
// ---------------------------------------------------------------------------

function AddSheet({ categories, onClose, onExtracted, onSaveManual }) {
  const [tab, setTab] = useState('url'); // url | manual
  return (
    <div className="fixed inset-0 bg-ink/60 backdrop-blur-sm z-40 flex items-stretch sm:items-center justify-center sm:p-4 fade-in">
      <div className="bg-cream w-full sm:max-w-2xl sm:rounded-3xl shadow-2xl overflow-hidden flex flex-col max-h-screen sm:max-h-[92vh]">
        <div className="flex items-center justify-between p-4 border-b border-ink/10">
          <button onClick={onClose} className="flex items-center gap-1.5 text-ink/70 hover:text-ink text-sm">
            <X size={18} /> סגירה
          </button>
          <h2 className="font-display text-lg font-bold">מתכון חדש</h2>
          <div className="w-12" />
        </div>

        <div className="flex border-b border-ink/10 px-4 gap-1 bg-cream sticky top-0">
          {[
            { id: 'url',    label: 'מקישור', icon: <Sparkles size={15} /> },
            { id: 'manual', label: 'ידני',   icon: <Edit2 size={15} /> },
          ].map(t => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`flex items-center gap-1.5 px-3 py-3 text-sm font-medium border-b-2 transition ${
                tab === t.id ? 'border-terracotta text-terracotta' : 'border-transparent text-ink/60 hover:text-ink'
              }`}
            >
              {t.icon} {t.label}
            </button>
          ))}
        </div>

        <div className="overflow-y-auto flex-1">
          {tab === 'url' ? (
            <UrlExtract onExtracted={onExtracted} />
          ) : (
            <ManualForm categories={categories} onSave={onSaveManual} />
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// UrlExtract: paste URL, pick providers, run extraction, hand off to review
// ---------------------------------------------------------------------------

function UrlExtract({ onExtracted }) {
  const [url, setUrl] = useState('');
  const [providers, setProviders] = useState([]); // [{id, name, enabled, model}]
  const [picked, setPicked] = useState(new Set());
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState('idle'); // idle | capturing | extracting
  const [err, setErr] = useState('');

  useEffect(() => {
    api.providers()
      .then(r => {
        setProviders(r.providers);
        // Default-select all enabled providers
        const en = new Set(r.providers.filter(p => p.enabled).map(p => p.id));
        setPicked(en);
      })
      .catch(() => setProviders([]));
  }, []);

  const togglePicked = (id) => {
    setPicked(s => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const run = async () => {
    setErr('');
    if (!url.trim()) { setErr('נא להזין כתובת'); return; }
    if (picked.size === 0) { setErr('נא לבחור לפחות ספק LLM אחד'); return; }
    setBusy(true);
    setPhase('capturing');
    try {
      // The /extract endpoint does capture + extraction together
      setTimeout(() => setPhase('extracting'), 4000); // visual hint that we're past capture
      const data = await api.extract(url.trim(), Array.from(picked));
      onExtracted({
        captureSessionId: data.capture?.session_id,
        capture: data.capture,
        sourceUrl: data.url,
        sourceDomain: data.source_domain,
        pageTitle: data.page_title,
        results: data.results,
      });
    } catch (e) {
      setErr(e.message || 'שגיאה');
    }
    setBusy(false);
    setPhase('idle');
  };

  const enabledProviders = providers.filter(p => p.enabled);
  const disabledProviders = providers.filter(p => !p.enabled);

  if (busy) {
    return (
      <div className="p-10 text-center">
        <div className="inline-flex items-center justify-center w-16 h-16 rounded-full bg-terracotta/10 text-terracotta mb-5 pulse-soft">
          {phase === 'capturing' ? <Camera size={28} /> : <Sparkles size={28} />}
        </div>
        <h3 className="font-display text-xl font-bold mb-2">
          {phase === 'capturing' ? 'מצלם את הדף...' : 'מחלץ את המתכון...'}
        </h3>
        <p className="text-sm text-ink/60">
          {phase === 'capturing'
            ? 'פותח את הדף ושומר PDF + צילום מסך'
            : 'שולח לספק/י LLM שבחרת'}
        </p>
        <p className="text-xs text-ink/40 mt-4">בדרך כלל לוקח 5–20 שניות</p>
      </div>
    );
  }

  return (
    <div className="p-5 sm:p-7 space-y-5">
      <Field label="קישור למתכון" required hint="האתר ייפתח בדפדפן פנימי, נשמר ל-PDF + צילום מסך, והתוכן יישלח ל-LLM לחילוץ">
        <input
          type="url"
          dir="ltr"
          value={url}
          onChange={e => setUrl(e.target.value)}
          placeholder="https://example.com/recipe"
          className="w-full px-3 py-2.5 bg-white border border-ink/15 rounded-xl text-sm focus:outline-none focus:border-terracotta focus:ring-2 focus:ring-terracotta/15"
          style={{ direction: url ? 'ltr' : 'rtl', textAlign: url ? 'left' : 'right' }}
        />
      </Field>

      <Field label="ספקי LLM לחילוץ" hint={picked.size > 1 ? `יורצו ${picked.size} ספקים במקביל. תקבלי השוואה.` : 'בחירת יותר מאחד תאפשר השוואה'}>
        <div className="space-y-2">
          {enabledProviders.length === 0 && (
            <div className="text-sm text-amber-900 bg-amber-50 border border-amber-200 rounded-lg p-3">
              ⚠️ אין ספק LLM פעיל. הגדר/י <code className="font-mono text-xs">ANTHROPIC_API_KEY</code> או <code className="font-mono text-xs">OPENAI_API_KEY</code> ב-Secret של ה-k8s.
            </div>
          )}
          {enabledProviders.map(p => (
            <label key={p.id} className="flex items-center gap-3 p-3 bg-white border border-ink/10 rounded-xl cursor-pointer hover:border-ink/25">
              <input
                type="checkbox"
                checked={picked.has(p.id)}
                onChange={() => togglePicked(p.id)}
                className="w-4 h-4 accent-terracotta"
              />
              <div className="flex-1">
                <div className="font-medium text-sm">{p.name}</div>
                <div className="text-xs text-ink/50 font-mono">{p.model}</div>
              </div>
            </label>
          ))}
          {disabledProviders.map(p => (
            <div key={p.id} className="flex items-center gap-3 p-3 bg-ink/[0.03] rounded-xl opacity-60">
              <div className="w-4 h-4 rounded border border-ink/20" />
              <div className="flex-1">
                <div className="font-medium text-sm">{p.name}</div>
                <div className="text-xs text-ink/50">לא מוגדר API key</div>
              </div>
            </div>
          ))}
        </div>
      </Field>

      {err && (
        <div className="flex items-start gap-2 text-sm text-red-900 bg-red-50 border border-red-200 rounded-lg p-3">
          <AlertCircle size={16} className="mt-0.5 shrink-0" />
          <span>{err}</span>
        </div>
      )}

      <button
        onClick={run}
        disabled={!url.trim() || picked.size === 0}
        className="w-full bg-terracotta hover:bg-terracotta-dark disabled:opacity-40 disabled:cursor-not-allowed text-white py-3 rounded-xl font-medium transition flex items-center justify-center gap-2"
      >
        <Sparkles size={18} />
        צלם וחלץ מתכון
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ReviewAndSave: show LLM results side-by-side, let user pick/merge, save
// ---------------------------------------------------------------------------

function ReviewAndSave({ draft, categories, onCancel, onBack, onSave }) {
  const successes = draft.results.filter(r => r.success);
  const failures = draft.results.filter(r => !r.success);

  // Initial form: longest non-empty field per result (a simple merge)
  const initial = useMemo(() => {
    const merge = {
      title: '', category: 'other', ingredients: '', instructions: '', notes: '',
    };
    for (const r of successes) {
      const d = r.data || {};
      for (const k of Object.keys(merge)) {
        const v = (d[k] || '').toString();
        if (v.length > (merge[k] || '').length) merge[k] = v;
      }
    }
    return merge;
  }, [draft]);

  const [view, setView] = useState(successes.length > 1 ? 'compare' : 'form');
  const [form, setForm] = useState({
    title: initial.title,
    category: initial.category,
    url: draft.sourceUrl,
    ingredients: initial.ingredients,
    instructions: initial.instructions,
    notes: initial.notes,
    added_by: 'baseline',
    date: new Date().toISOString().slice(0, 10),
  });

  const pickResult = (r) => {
    setForm(f => ({
      ...f,
      title: r.data.title || f.title,
      category: r.data.category || f.category,
      ingredients: r.data.ingredients || f.ingredients,
      instructions: r.data.instructions || f.instructions,
      notes: r.data.notes || f.notes,
    }));
    setView('form');
  };

  const useMerged = () => setView('form'); // form is already populated with merged values

  if (view === 'compare') {
    return (
      <div className="fixed inset-0 bg-ink/60 backdrop-blur-sm z-40 flex items-stretch sm:items-center justify-center sm:p-4 fade-in">
        <div className="bg-cream w-full sm:max-w-5xl sm:rounded-3xl shadow-2xl overflow-hidden flex flex-col max-h-screen sm:max-h-[92vh]">
          <div className="flex items-center justify-between p-4 border-b border-ink/10">
            <button onClick={onBack} className="flex items-center gap-1.5 text-ink/70 hover:text-ink text-sm">
              <ArrowLeft size={18} /> חזרה
            </button>
            <h2 className="font-display text-lg font-bold">השוואת תוצאות</h2>
            <button onClick={onCancel} className="text-ink/70 hover:text-ink text-sm">ביטול</button>
          </div>

          <div className="overflow-y-auto flex-1 p-4 sm:p-6">
            {draft.capture?.screenshot_url && (
              <div className="mb-5">
                <p className="text-xs text-ink/55 mb-2 flex items-center gap-1.5">
                  <Camera size={13} /> צילום מסך של הדף המקורי:
                </p>
                <a href={draft.capture.screenshot_url} target="_blank" rel="noopener noreferrer">
                  <img src={draft.capture.screenshot_url} alt="screenshot"
                       className="max-h-40 rounded-xl border border-ink/10 hover:opacity-90 transition" />
                </a>
              </div>
            )}

            {failures.length > 0 && (
              <div className="mb-4 text-sm text-amber-900 bg-amber-50 border border-amber-200 rounded-lg p-3">
                {failures.map(f => (
                  <div key={f.provider}>⚠️ {f.provider}: {f.error}</div>
                ))}
              </div>
            )}

            {successes.length === 0 ? (
              <div className="text-center py-10 text-ink/60">
                לא הצלחתי לחלץ מתכון. נסי שוב או בחרי "מילוי ידני".
              </div>
            ) : (
              <>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {successes.map(r => (
                    <ResultCard key={r.provider} result={r} onPick={() => pickResult(r)} />
                  ))}
                </div>
                <div className="mt-5 flex flex-col sm:flex-row gap-3">
                  <button
                    onClick={useMerged}
                    className="flex-1 bg-terracotta hover:bg-terracotta-dark text-white py-3 rounded-xl font-medium transition flex items-center justify-center gap-2"
                  >
                    <Sparkles size={16} />
                    מיזוג חכם של כולם
                  </button>
                  <p className="text-xs text-ink/55 text-center sm:text-right max-w-xs self-center">
                    לוקח את השדה הארוך/השלם ביותר מכל ספק
                  </p>
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    );
  }

  // view === 'form'
  return (
    <RecipeForm
      initial={form}
      categories={categories}
      title="עריכה לפני שמירה"
      onCancel={onCancel}
      onSave={onSave}
      extraPanel={
        <div className="bg-white rounded-xl p-3 border border-ink/10 mb-4 text-xs flex items-center gap-2 flex-wrap">
          <span className="text-ink/55">חולץ מ:</span>
          <span className="font-mono">{draft.sourceDomain}</span>
          {draft.capture?.has_pdf && <span className="bg-ink/[0.06] px-2 py-0.5 rounded-full flex items-center gap-1"><FileText size={11} /> PDF</span>}
          {draft.capture?.has_screenshot && <span className="bg-ink/[0.06] px-2 py-0.5 rounded-full flex items-center gap-1"><Camera size={11} /> צילום</span>}
          {successes.length > 1 && (
            <button onClick={() => setView('compare')} className="text-terracotta hover:underline ms-auto">השוואת ספקים →</button>
          )}
        </div>
      }
    />
  );
}

function ResultCard({ result, onPick }) {
  const d = result.data || {};
  return (
    <div className="bg-white rounded-2xl border border-ink/10 p-4 flex flex-col">
      <div className="flex items-center justify-between mb-3">
        <div>
          <div className="font-medium text-sm">{result.provider === 'anthropic' ? 'Claude (Anthropic)' : result.provider === 'openai' ? 'GPT (OpenAI)' : result.provider}</div>
          <div className="text-xs text-ink/50">{result.elapsed_ms}ms</div>
        </div>
        <button
          onClick={onPick}
          className="text-xs bg-terracotta hover:bg-terracotta-dark text-white px-3 py-1.5 rounded-full transition flex items-center gap-1"
        >
          <Check size={12} />
          השתמש בזה
        </button>
      </div>
      <div className="space-y-2.5 text-sm flex-1">
        <div>
          <div className="text-[11px] text-ink/50 uppercase tracking-wide">כותרת</div>
          <div className="font-medium">{d.title || <span className="text-ink/30">—</span>}</div>
        </div>
        <div>
          <div className="text-[11px] text-ink/50 uppercase tracking-wide">קטגוריה</div>
          <div>{d.category || <span className="text-ink/30">—</span>}</div>
        </div>
        <div>
          <div className="text-[11px] text-ink/50 uppercase tracking-wide">רכיבים</div>
          <pre className="whitespace-pre-wrap font-body text-[13px] leading-relaxed text-ink/85 max-h-32 overflow-y-auto">{d.ingredients || '—'}</pre>
        </div>
        <div>
          <div className="text-[11px] text-ink/50 uppercase tracking-wide">הוראות</div>
          <pre className="whitespace-pre-wrap font-body text-[13px] leading-relaxed text-ink/85 max-h-32 overflow-y-auto">{d.instructions || '—'}</pre>
        </div>
        {d.notes && (
          <div>
            <div className="text-[11px] text-ink/50 uppercase tracking-wide">הערות</div>
            <div className="text-[13px] text-ink/80">{d.notes}</div>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ManualForm: blank form for manual entry
// ---------------------------------------------------------------------------

function ManualForm({ categories, onSave }) {
  const initial = {
    title: '', category: 'desserts', url: '',
    ingredients: '', instructions: '', notes: '',
    added_by: 'baseline', date: new Date().toISOString().slice(0, 10),
  };
  return <RecipeFormInner initial={initial} categories={categories} onSave={onSave} />;
}

// ---------------------------------------------------------------------------
// RecipeForm (modal wrapper + reusable form)
// ---------------------------------------------------------------------------

function RecipeForm({ initial, categories, title, onCancel, onSave, extraPanel }) {
  return (
    <div className="fixed inset-0 bg-ink/60 backdrop-blur-sm z-40 flex items-stretch sm:items-center justify-center sm:p-4 fade-in">
      <div className="bg-cream w-full sm:max-w-2xl sm:rounded-3xl shadow-2xl overflow-hidden flex flex-col max-h-screen sm:max-h-[92vh]">
        <div className="flex items-center justify-between p-4 border-b border-ink/10">
          <button onClick={onCancel} className="flex items-center gap-1.5 text-ink/70 hover:text-ink text-sm">
            <X size={18} /> ביטול
          </button>
          <h2 className="font-display text-lg font-bold">{title || 'מתכון'}</h2>
          <div className="w-12" />
        </div>
        <div className="overflow-y-auto flex-1">
          {extraPanel && <div className="px-5 sm:px-7 pt-4">{extraPanel}</div>}
          <RecipeFormInner initial={initial} categories={categories} onSave={onSave} />
        </div>
      </div>
    </div>
  );
}

function RecipeFormInner({ initial, categories, onSave }) {
  const [form, setForm] = useState(initial);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (!form.title.trim()) { alert('יש לכתוב שם למתכון'); return; }
    setBusy(true);
    await onSave(form);
    setBusy(false);
  };

  return (
    <div className="p-5 sm:p-7 space-y-5">
      <Field label="שם המתכון" required>
        <input
          type="text"
          value={form.title}
          onChange={e => setForm({ ...form, title: e.target.value })}
          placeholder="למשל: עוגת שוקולד של סבתא"
          className="w-full px-3 py-2.5 bg-white border border-ink/15 rounded-xl focus:outline-none focus:border-terracotta focus:ring-2 focus:ring-terracotta/15"
        />
      </Field>

      <div className="grid grid-cols-2 gap-4">
        <Field label="קטגוריה">
          <select
            value={form.category}
            onChange={e => setForm({ ...form, category: e.target.value })}
            className="w-full px-3 py-2.5 bg-white border border-ink/15 rounded-xl focus:outline-none focus:border-terracotta focus:ring-2 focus:ring-terracotta/15"
          >
            {categories.map(c => (
              <option key={c.id} value={c.id}>{c.emoji} {c.label}</option>
            ))}
          </select>
        </Field>
        <Field label="הוסיף">
          <select
            value={form.added_by}
            onChange={e => setForm({ ...form, added_by: e.target.value })}
            className="w-full px-3 py-2.5 bg-white border border-ink/15 rounded-xl focus:outline-none focus:border-terracotta focus:ring-2 focus:ring-terracotta/15"
          >
            <option value="baseline">baseline</option>
            <option value="User2">User2</option>
            <option value="both">both</option>
          </select>
        </Field>
      </div>

      <Field label="קישור (אופציונלי)">
        <input
          type="url"
          dir="ltr"
          value={form.url || ''}
          onChange={e => setForm({ ...form, url: e.target.value })}
          placeholder="https://..."
          className="w-full px-3 py-2.5 bg-white border border-ink/15 rounded-xl text-sm focus:outline-none focus:border-terracotta focus:ring-2 focus:ring-terracotta/15"
          style={{ direction: form.url ? 'ltr' : 'rtl', textAlign: form.url ? 'left' : 'right' }}
        />
      </Field>

      <Field label="רכיבים">
        <textarea
          value={form.ingredients}
          onChange={e => setForm({ ...form, ingredients: e.target.value })}
          placeholder="• 200 גרם חמאה&#10;• 1 כוס סוכר..."
          rows={7}
          className="w-full px-3 py-2.5 bg-white border border-ink/15 rounded-xl focus:outline-none focus:border-terracotta focus:ring-2 focus:ring-terracotta/15 leading-relaxed"
        />
      </Field>

      <Field label="הוראות הכנה">
        <textarea
          value={form.instructions}
          onChange={e => setForm({ ...form, instructions: e.target.value })}
          placeholder="1. מערבבים בקערה...&#10;2. אופים..."
          rows={8}
          className="w-full px-3 py-2.5 bg-white border border-ink/15 rounded-xl focus:outline-none focus:border-terracotta focus:ring-2 focus:ring-terracotta/15 leading-relaxed"
        />
      </Field>

      <Field label="הערות">
        <textarea
          value={form.notes}
          onChange={e => setForm({ ...form, notes: e.target.value })}
          placeholder="טיפים, התאמות..."
          rows={3}
          className="w-full px-3 py-2.5 bg-white border border-ink/15 rounded-xl focus:outline-none focus:border-terracotta focus:ring-2 focus:ring-terracotta/15"
        />
      </Field>

      <div className="sticky bottom-0 -mx-5 sm:-mx-7 px-5 sm:px-7 py-3 bg-cream border-t border-ink/10">
        <button
          onClick={submit}
          disabled={busy || !form.title.trim()}
          className="w-full bg-terracotta hover:bg-terracotta-dark disabled:opacity-40 text-white py-3 rounded-xl font-medium transition flex items-center justify-center gap-2"
        >
          {busy ? <Loader2 size={16} className="animate-spin" /> : <Check size={16} />}
          שמירה
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// RecipeDetail with tabs: structured / pdf / screenshot
// ---------------------------------------------------------------------------

function RecipeDetail({ recipe, category, onClose, onEdit, onDelete, onUpdate, onShare, showToast }) {
  const [tab, setTab] = useState('structured');
  const [shareMenu, setShareMenu] = useState(false);
  const [shareUrl, setShareUrl] = useState(null);
  const [recapturing, setRecapturing] = useState(false);
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef(null);

  const ensureShareUrl = async () => {
    if (shareUrl) return shareUrl;
    const { share_url } = await api.createShare(recipe.id);
    setShareUrl(share_url);
    return share_url;
  };

  const handleCopyText = async () => {
    let url = shareUrl;
    try { url = await ensureShareUrl(); } catch (_) {}
    const txt = formatForShare(recipe, category, url);
    await navigator.clipboard.writeText(txt);
    showToast('הועתק! אפשר להדביק בוואטסאפ 📋');
    setShareMenu(false);
  };

  const handleCopyShareLink = async () => {
    try {
      const url = await ensureShareUrl();
      await navigator.clipboard.writeText(url);
      showToast('הקישור הועתק 🔗');
    } catch (e) {
      showToast(`שגיאה: ${e.message}`);
    }
    setShareMenu(false);
  };

  const handleRecapture = async () => {
    if (!recipe.url) { showToast('אין URL למתכון'); return; }
    setRecapturing(true);
    try {
      const updated = await api.recapture(recipe.id);
      onUpdate(updated);
      showToast('הdf וצילום המסך עודכנו ✓');
    } catch (e) {
      showToast(`שגיאה: ${e.message}`);
    }
    setRecapturing(false);
  };

  const handleImageUpload = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    try {
      const updated = await api.uploadImage(recipe.id, file);
      onUpdate(updated);
      showToast('התמונה הועלתה ✓');
    } catch (err) {
      showToast(`שגיאה: ${err.message}`);
    }
    setUploading(false);
    e.target.value = '';
  };

  return (
    <div className="fixed inset-0 bg-ink/60 backdrop-blur-sm z-40 flex items-stretch sm:items-center justify-center sm:p-4 fade-in">
      <div className="bg-cream w-full sm:max-w-4xl sm:rounded-3xl shadow-2xl overflow-hidden flex flex-col max-h-screen sm:max-h-[92vh]">

        {/* Top bar */}
        <div className="flex items-center justify-between p-3 sm:p-4 bg-cream border-b border-ink/10 no-print">
          <button onClick={onClose} className="flex items-center gap-1.5 text-ink/70 hover:text-ink px-2 py-1 rounded-full text-sm">
            <ArrowLeft size={18} /> חזרה
          </button>
          <div className="flex items-center gap-0.5">
            <IconBtn onClick={() => fileRef.current?.click()} icon={<ImagePlus size={17} />} label="העלאת תמונה" busy={uploading} />
            <input ref={fileRef} type="file" accept="image/*" className="hidden" onChange={handleImageUpload} />
            <IconBtn onClick={onEdit} icon={<Edit2 size={17} />} label="ערוך" />
            <IconBtn onClick={() => window.print()} icon={<Printer size={17} />} label="הדפסה" />
            <div className="relative">
              <IconBtn onClick={() => setShareMenu(s => !s)} icon={<Share2 size={17} />} label="שתף" />
              {shareMenu && (
                <div className="absolute left-0 top-full mt-1 bg-white rounded-xl shadow-xl border border-ink/10 py-1 min-w-[240px] z-10 fade-in">
                  <ShareMenuItem onClick={handleCopyText} icon={<Copy size={15} />}
                                 title="העתק לוואטסאפ" subtitle="טקסט מעוצב + קישור צפייה" />
                  <ShareMenuItem onClick={handleCopyShareLink} icon={<Link2 size={15} />}
                                 title="העתק קישור צפייה" subtitle="פתיחה גם ללא חשבון" />
                  {recipe.has_pdf && (
                    <ShareMenuItem
                      onClick={() => { window.open(recipe.pdf_url, '_blank'); setShareMenu(false); }}
                      icon={<FileDown size={15} />}
                      title="הורדת PDF" subtitle="הדף המקורי כפי שנשמר"
                    />
                  )}
                </div>
              )}
            </div>
            <IconBtn onClick={() => onDelete(recipe.id)} icon={<Trash2 size={17} />} label="מחק" danger />
          </div>
        </div>

        {/* Tab strip */}
        <div className="flex border-b border-ink/10 px-4 gap-1 bg-cream no-print">
          <DetailTab id="structured" current={tab} onSelect={setTab} icon={<BookOpen size={14} />} label="מתכון" />
          {recipe.has_screenshot && (
            <DetailTab id="screenshot" current={tab} onSelect={setTab} icon={<Camera size={14} />} label="צילום מסך" />
          )}
          {recipe.has_pdf && (
            <DetailTab id="pdf" current={tab} onSelect={setTab} icon={<FileText size={14} />} label="PDF מקורי" />
          )}
          {recipe.url && !recipe.has_pdf && !recipe.has_screenshot && (
            <button onClick={handleRecapture} disabled={recapturing}
              className="me-auto flex items-center gap-1.5 px-3 py-3 text-sm text-terracotta hover:text-terracotta-dark transition disabled:opacity-50">
              {recapturing ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
              צילום הדף עכשיו
            </button>
          )}
          {(recipe.has_pdf || recipe.has_screenshot) && (
            <button onClick={handleRecapture} disabled={recapturing}
              className="me-auto flex items-center gap-1.5 px-3 py-3 text-sm text-ink/55 hover:text-ink transition disabled:opacity-50"
              title="צילום מחדש של הדף">
              {recapturing ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
            </button>
          )}
        </div>

        {/* Content */}
        <div className="overflow-y-auto flex-1">
          {tab === 'structured' && (
            <StructuredView recipe={recipe} category={category} />
          )}
          {tab === 'pdf' && recipe.pdf_url && (
            <div className="h-full bg-ink/5">
              <iframe src={recipe.pdf_url} title="PDF" className="w-full h-full min-h-[600px] border-0" />
              <div className="p-3 text-center">
                <a href={recipe.pdf_url} target="_blank" rel="noopener noreferrer"
                   className="inline-flex items-center gap-1.5 text-sm text-terracotta hover:underline">
                  <FileDown size={14} /> פתיחה / הורדה
                </a>
              </div>
            </div>
          )}
          {tab === 'screenshot' && recipe.screenshot_url && (
            <div className="bg-ink/5 p-2 sm:p-4">
              <img src={recipe.screenshot_url} alt="screenshot"
                   className="w-full rounded-xl border border-ink/10 bg-white shadow-sm" />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function DetailTab({ id, current, onSelect, icon, label }) {
  const active = id === current;
  return (
    <button
      onClick={() => onSelect(id)}
      className={`flex items-center gap-1.5 px-3 py-3 text-sm font-medium border-b-2 transition ${
        active ? 'border-terracotta text-terracotta' : 'border-transparent text-ink/60 hover:text-ink'
      }`}
    >
      {icon} {label}
    </button>
  );
}

function ShareMenuItem({ onClick, icon, title, subtitle }) {
  return (
    <button onClick={onClick}
      className="w-full flex items-center gap-2 px-3 py-2 text-sm hover:bg-cream text-right">
      {icon}
      <div className="flex-1 text-right">
        <div>{title}</div>
        {subtitle && <div className="text-xs text-ink/50">{subtitle}</div>}
      </div>
    </button>
  );
}

function StructuredView({ recipe, category }) {
  return (
    <div className="p-5 sm:p-8">
      {recipe.image_url && (
        <div className="aspect-[16/10] sm:aspect-[16/9] overflow-hidden bg-[#E8D9B8] rounded-2xl mb-5 -mx-5 sm:-mx-8 sm:mx-0">
          <img src={recipe.image_url} alt={recipe.title} className="w-full h-full object-cover" />
        </div>
      )}

      <div className="flex items-center gap-2 mb-3 flex-wrap">
        {category && (
          <span className="inline-flex items-center gap-1 bg-ink/[0.06] px-2.5 py-1 rounded-full text-xs">
            <span>{category.emoji}</span><span>{category.label}</span>
          </span>
        )}
        {recipe.added_by && <span className="text-xs text-ink/55">נוסף ע"י {recipe.added_by}</span>}
        {recipe.date && <span className="text-xs text-ink/55">· {formatDate(recipe.date)}</span>}
      </div>

      <h1 className="font-display text-3xl sm:text-4xl font-bold leading-tight mb-4">{recipe.title}</h1>

      {recipe.url && (
        <a href={recipe.url} target="_blank" rel="noopener noreferrer"
           className="no-print inline-flex items-center gap-1.5 text-sm text-terracotta hover:underline mb-6">
          <ExternalLink size={14} />
          פתח את המתכון המקורי
          <span className="text-ink/40">· {getDomain(recipe.url)}</span>
        </a>
      )}

      {recipe.notes && (
        <div className="bg-white border-r-4 border-terracotta rounded-l-xl rounded-r-sm p-4 mb-6 text-ink/85 text-[15px] leading-relaxed">
          {recipe.notes}
        </div>
      )}

      {recipe.ingredients?.trim() && (
        <section className="mb-7">
          <h2 className="font-display text-xl font-bold mb-3 flex items-center gap-2">
            <span className="text-terracotta">📝</span> רכיבים
          </h2>
          <div className="whitespace-pre-wrap text-[15px] leading-[1.85] text-ink/90">{recipe.ingredients}</div>
        </section>
      )}

      {recipe.instructions?.trim() && (
        <section className="mb-4">
          <h2 className="font-display text-xl font-bold mb-3 flex items-center gap-2">
            <span className="text-terracotta">👨‍🍳</span> הוראות הכנה
          </h2>
          <div className="whitespace-pre-wrap text-[15px] leading-[1.85] text-ink/90">{recipe.instructions}</div>
        </section>
      )}

      {!recipe.ingredients?.trim() && !recipe.instructions?.trim() && (
        <div className="bg-amber-50 border border-amber-200 rounded-xl p-4 text-sm text-amber-900">
          💡 אין רכיבים/הוראות מובניים. {recipe.has_pdf || recipe.has_screenshot
            ? 'אפשר לראות את הדף המקורי בלשונית PDF / צילום מסך.'
            : recipe.url
              ? 'אפשר ללחוץ על "פתח מתכון מקורי" או לערוך ולמלא ידנית.'
              : 'אפשר לערוך ולמלא ידנית.'}
        </div>
      )}
    </div>
  );
}
