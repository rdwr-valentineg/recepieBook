import React, { useState, useEffect } from 'react';
import { FileText, Camera, ExternalLink, ChefHat, Loader2, AlertCircle, BookOpen } from 'lucide-react';
import { api } from './api.js';

const getDomain = (url) => {
  if (!url) return '';
  try { return new URL(url).hostname.replace(/^www\./, '').replace(/^mobile\./, ''); } catch { return ''; }
};
const formatDate = (iso) => {
  if (!iso) return '';
  try { return new Date(iso).toLocaleDateString('he-IL', { day: 'numeric', month: 'short', year: 'numeric' }); } catch { return iso; }
};

const CATEGORIES = {
  desserts:  { label: 'עוגות וקינוחים', emoji: '🍰' },
  pastries:  { label: 'מאפים ועוגיות', emoji: '🥧' },
  bread:     { label: 'לחמים',          emoji: '🍞' },
  meat:      { label: 'בשר ועוף',       emoji: '🍗' },
  fish:      { label: 'דגים',           emoji: '🐟' },
  salads:    { label: 'סלטים',          emoji: '🥗' },
  pasta:     { label: 'פסטה ואורז',     emoji: '🍝' },
  soups:     { label: 'מרקים',          emoji: '🍲' },
  stews:     { label: 'תבשילים',        emoji: '🥘' },
  breakfast: { label: 'ארוחת בוקר',     emoji: '🍳' },
  drinks:    { label: 'שתייה',          emoji: '🥤' },
  other:     { label: 'שונות',          emoji: '📌' },
};

export default function ShareView({ token }) {
  const [recipe, setRecipe] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [tab, setTab] = useState('structured');

  useEffect(() => {
    api.shareGet(token)
      .then(r => { setRecipe(r); setLoading(false); })
      .catch(e => { setError(e.message); setLoading(false); });
  }, [token]);

  if (loading) return (
    <div dir="rtl" className="min-h-screen paper-bg flex items-center justify-center">
      <Loader2 className="animate-spin text-terracotta" size={32} />
    </div>
  );

  if (error) return (
    <div dir="rtl" className="min-h-screen paper-bg flex items-center justify-center p-6">
      <div className="text-center">
        <div className="text-5xl mb-4">🔒</div>
        <h1 className="font-display text-2xl font-bold mb-2">קישור לא תקף</h1>
        <p className="text-ink/60 text-sm">{error}</p>
      </div>
    </div>
  );

  const cat = CATEGORIES[recipe.category] || CATEGORIES.other;
  const tabs = [
    { id: 'structured', label: 'מתכון',       icon: <BookOpen size={14} /> },
    ...(recipe.has_screenshot ? [{ id: 'screenshot', label: 'צילום מסך', icon: <Camera size={14} /> }] : []),
    ...(recipe.has_pdf        ? [{ id: 'pdf',        label: 'PDF מקורי', icon: <FileText size={14} /> }] : []),
  ];

  return (
    <div dir="rtl" className="min-h-screen paper-bg text-ink">
      {/* Header */}
      <header className="border-b border-ink/10 bg-cream/95 backdrop-blur sticky top-0 z-10">
        <div className="max-w-3xl mx-auto px-4 py-4 flex items-center gap-3">
          <div className="w-9 h-9 rounded-full bg-terracotta flex items-center justify-center text-white shrink-0">
            <ChefHat size={18} />
          </div>
          <div className="min-w-0">
            <h1 className="font-display text-base font-bold leading-tight truncate">{recipe.title}</h1>
            <p className="text-xs text-ink/55">{cat.emoji} {cat.label}</p>
          </div>
        </div>
      </header>

      {/* Tab strip */}
      {tabs.length > 1 && (
        <div className="max-w-3xl mx-auto px-4 flex gap-1 border-b border-ink/10">
          {tabs.map(t => (
            <button key={t.id} onClick={() => setTab(t.id)}
              className={`flex items-center gap-1.5 px-3 py-3 text-sm font-medium border-b-2 transition ${
                tab === t.id ? 'border-terracotta text-terracotta' : 'border-transparent text-ink/60 hover:text-ink'
              }`}
            >
              {t.icon} {t.label}
            </button>
          ))}
        </div>
      )}

      {/* Content */}
      <main className="max-w-3xl mx-auto">
        {tab === 'structured' && (
          <div className="px-4 sm:px-6 py-6">
            {recipe.image_url && (
              <div className="aspect-[16/9] overflow-hidden bg-[#E8D9B8] rounded-2xl mb-5 -mx-4 sm:mx-0">
                <img src={recipe.image_url} alt={recipe.title} className="w-full h-full object-cover" />
              </div>
            )}

            <div className="flex items-center gap-2 mb-3 flex-wrap">
              <span className="inline-flex items-center gap-1 bg-ink/[0.06] px-2.5 py-1 rounded-full text-xs">
                <span>{cat.emoji}</span><span>{cat.label}</span>
              </span>
              {recipe.added_by && <span className="text-xs text-ink/55">נוסף ע"י {recipe.added_by}</span>}
              {recipe.date && <span className="text-xs text-ink/55">· {formatDate(recipe.date)}</span>}
            </div>

            <h2 className="font-display text-3xl sm:text-4xl font-bold leading-tight mb-4">{recipe.title}</h2>

            {recipe.url && (
              <a href={recipe.url} target="_blank" rel="noopener noreferrer"
                 className="inline-flex items-center gap-1.5 text-sm text-terracotta hover:underline mb-6">
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
                <h3 className="font-display text-xl font-bold mb-3 flex items-center gap-2">
                  <span className="text-terracotta">📝</span> רכיבים
                </h3>
                <div className="whitespace-pre-wrap text-[15px] leading-[1.85] text-ink/90">{recipe.ingredients}</div>
              </section>
            )}

            {recipe.instructions?.trim() && (
              <section className="mb-6">
                <h3 className="font-display text-xl font-bold mb-3 flex items-center gap-2">
                  <span className="text-terracotta">👨‍🍳</span> הוראות הכנה
                </h3>
                <div className="whitespace-pre-wrap text-[15px] leading-[1.85] text-ink/90">{recipe.instructions}</div>
              </section>
            )}

            {recipe.has_pdf && (
              <div className="mt-6 pt-6 border-t border-ink/10">
                <a href={recipe.pdf_url} target="_blank" rel="noopener noreferrer"
                   className="inline-flex items-center gap-2 bg-white border border-ink/15 hover:border-ink/30 px-4 py-2.5 rounded-xl text-sm transition">
                  <FileText size={16} className="text-terracotta" />
                  הורדת הדף המקורי (PDF)
                </a>
              </div>
            )}

            <div className="mt-8 pt-6 border-t border-ink/10 text-xs text-ink/40 text-center">
              שותף מתוך ספר המתכונים המשפחתי 🥘
            </div>
          </div>
        )}

        {tab === 'screenshot' && recipe.screenshot_url && (
          <div className="bg-ink/5 p-2 sm:p-4">
            <img src={recipe.screenshot_url} alt="screenshot"
                 className="w-full rounded-xl border border-ink/10 bg-white shadow-sm" />
          </div>
        )}

        {tab === 'pdf' && recipe.pdf_url && (
          <div className="h-screen">
            <iframe src={recipe.pdf_url} title="PDF" className="w-full h-full min-h-[600px] border-0" />
          </div>
        )}
      </main>
    </div>
  );
}
