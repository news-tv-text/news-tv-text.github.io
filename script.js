// ---- Teletext News TV ----

let data = null;
let currentPage = 'headlines';

// Parse analysis text into sections
function parseAnalysis(text) {
  if (!text) return {};

  const sections = {};
  const lines = text.split('\n');
  let currentSection = null;
  let currentContent = [];

  for (const line of lines) {
    const headerMatch = line.match(/^#{1,3}\s+(.+)/);
    if (headerMatch) {
      if (currentSection) {
        sections[currentSection] = currentContent.join('\n');
      }
      currentSection = headerMatch[1].trim();
      currentContent = [line];
    } else {
      currentContent.push(line);
    }
  }
  if (currentSection) {
    sections[currentSection] = currentContent.join('\n');
  }
  return sections;
}

// Format analysis text as HTML
function formatText(text) {
  if (!text) return '<div class="loading">[ нет данных ]</div>';

  let html = text;

  // Headers
  html = html.replace(/^#### (.+)$/gm, '<h4>$1</h4>');
  html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
  html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
  html = html.replace(/^# (.+)$/gm, '<h2>$1</h2>');

  // Bold and italic
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');

  // Lists
  const lines = html.split('\n');
  let result = [];
  let inList = false;

  for (const line of lines) {
    const listMatch = line.match(/^(\s*)[-*]\s+(.+)/);
    if (listMatch) {
      if (!inList) {
        result.push('<ul>');
        inList = true;
      }
      result.push('<li>' + listMatch[2] + '</li>');
    } else {
      if (inList) {
        result.push('</ul>');
        inList = false;
      }
      const trimmed = line.trim();
      if (trimmed === '') {
        // skip empty lines inside lists
      } else if (trimmed.startsWith('<h') || trimmed.startsWith('</')) {
        result.push(line);
      } else if (trimmed === '---') {
        result.push('<hr>');
      } else {
        result.push('<p>' + trimmed + '</p>');
      }
    }
  }
  if (inList) result.push('</ul>');
  return result.join('\n');
}

// Extract numbered items (connections, insights) from text
function extractListItems(text) {
  if (!text) return [];
  const items = [];
  const lines = text.split('\n');
  let currentItem = '';
  let inItem = false;

  for (const line of lines) {
    const numberedMatch = line.match(/^(\d+)[\.\)]\s+(.+)/);
    const bulletMatch = line.match(/^[-*]\s+(.+)/);

    if (numberedMatch) {
      if (currentItem) items.push(currentItem);
      currentItem = numberedMatch[2];
      inItem = true;
    } else if (bulletMatch && !inItem) {
      if (currentItem) items.push(currentItem);
      currentItem = bulletMatch[1];
      inItem = true;
    } else if (inItem && line.trim()) {
      currentItem += ' ' + line.trim();
    } else if (inItem && !line.trim()) {
      items.push(currentItem);
      currentItem = '';
      inItem = false;
    }
  }
  if (currentItem) items.push(currentItem);
  return items;
}

// Build page: Headlines (key events)
function buildHeadlinesPage(sections) {
  let html = '';

  // Find key events sections
  for (const [title, content] of Object.entries(sections)) {
    const lower = title.toLowerCase();
    if (lower.includes('ключевые') || lower.includes('key event') || lower.includes('события')) {
      html += `<div class="page-section"><h2>${title}</h2>${formatText(content)}</div>`;
    }
  }

  // If no section found, show first part of analysis
  if (!html) {
    const firstSection = Object.values(sections)[0];
    if (firstSection) {
      html = `<div class="page-section">${formatText(firstSection)}</div>`;
    }
  }

  return html || '<div class="loading">[ нет данных ]</div>';
}

// Build page: Connections
function buildConnectionsPage(sections) {
  let html = '';

  for (const [title, content] of Object.entries(sections)) {
    const lower = title.toLowerCase();
    if (lower.includes('связь') || lower.includes('connection') || lower.includes('cross')) {
      html += `<div class="page-section"><h2>${title}</h2>${formatText(content)}</div>`;
    }
  }

  // Show trend sections too
  for (const [title, content] of Object.entries(sections)) {
    const lower = title.toLowerCase();
    if (lower.includes('паттерн') || lower.includes('pattern') || lower.includes('тренд')) {
      html += `<div class="page-section"><h2>${title}</h2>${formatText(content)}</div>`;
    }
  }

  return html || '<div class="loading">[ раздел не найден ]</div>';
}

// Build page: Insights
function buildInsightsPage(sections) {
  let html = '';

  for (const [title, content] of Object.entries(sections)) {
    const lower = title.toLowerCase();
    if (lower.includes('инсайт') || lower.includes('insight') || lower.includes('неожидан')) {
      html += `<div class="page-section"><h2>${title}</h2>${formatText(content)}</div>`;
    }
  }

  return html || '<div class="loading">[ раздел не найден ]</div>';
}

// Build page: Article feed
function buildArticlesPage(articles) {
  if (!articles || articles.length === 0) {
    return '<div class="loading">[ нет статей ]</div>';
  }

  let html = '<div class="page-section"><h2>последние статьи</h2>';
  for (const a of articles) {
    const date = a.published_at ? a.published_at.slice(0, 10) : '';
    html += `<div class="article-item">
      <div class="article-source">${escapeHtml(a.source)} | ${escapeHtml(date)}</div>
      <div class="article-title"><a href="${escapeHtml(a.url)}" target="_blank">${escapeHtml(a.title)}</a></div>
    </div>`;
  }
  html += '</div>';
  return html;
}

function escapeHtml(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// Render current page
function render() {
  if (!data) return;

  const content = document.getElementById('content');

  const analysisText = data.analysis?.text || '';
  const sections = parseAnalysis(analysisText);

  let html = '';
  switch (currentPage) {
    case 'headlines':
      html = buildHeadlinesPage(sections);
      break;
    case 'connections':
      html = buildConnectionsPage(sections);
      break;
    case 'insights':
      html = buildInsightsPage(sections);
      break;
    case 'articles':
      html = buildArticlesPage(data.recent_articles);
      break;
  }

  content.innerHTML = html || '<div class="loading">[ пусто ]</div>';
  content.scrollTop = 0;

  // Update footer
  const footer = document.getElementById('footer');
  if (footer) {
    const pages = ['headlines', 'connections', 'insights', 'articles'];
    const pageNum = pages.indexOf(currentPage) + 1;
    footer.innerHTML = `
      <span class="footer-left">PIPELINE: ${data.analysis?.created_at?.slice(0, 16) || '--'}</span>
      <span class="footer-center">ARTICLES: ${data.stats?.total_articles || 0}</span>
      <span class="footer-right">PAGE ${String(pageNum).padStart(2, '0')}/04</span>
    `;
  }
}

// Fetch data from data.json
async function fetchData() {
  try {
    const resp = await fetch('data.json?' + Date.now());
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    data = await resp.json();

    document.getElementById('updateInfo').textContent =
      'updated: ' + (data.updated_at || '--');

    render();
  } catch (err) {
    document.getElementById('content').innerHTML =
      `<div class="page-section"><p style="color:#ff3333;">[ connection error: ${err.message} ]</p>
       <p style="color:#558855;">waiting for next update...</p></div>`;
  }
}

// Clock
function updateClock() {
  const now = new Date();
  const time = now.toLocaleTimeString('ru-RU', { hour12: false });
  document.getElementById('time').textContent = time;
}

// Navigation
document.addEventListener('DOMContentLoaded', () => {
  // Clock
  updateClock();
  setInterval(updateClock, 1000);

  // Nav buttons
  document.querySelectorAll('.nav-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentPage = btn.dataset.page;
      render();
    });
  });

  // Initial fetch
  fetchData();

  // Auto-refresh every 60 seconds
  setInterval(fetchData, 60000);
});
