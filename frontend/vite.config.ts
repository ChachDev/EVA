import { defineConfig, loadEnv, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';
import { publicLocales, renderProductPage, renderRobots, renderSitemap, validatePublicOrigin } from './seo-pages.ts';

function productPages(origin: string): Plugin {
  return {
    name: 'eva-product-pages',
    configureServer(server) {
      server.middlewares.use((request, response, next) => {
        const pathname = new URL(request.url ?? '/', 'http://localhost').pathname;
        const locale = publicLocales.find(value => pathname === `/about/${value}/` || pathname === `/about/${value}/index.html`);
        const isSitemap = pathname === '/sitemap.xml' && !!origin;
        if (!locale && pathname !== '/robots.txt' && !isSitemap) return next();
        response.setHeader('Content-Type', locale ? 'text/html; charset=utf-8' : isSitemap ? 'application/xml; charset=utf-8' : 'text/plain; charset=utf-8');
        response.end(locale ? renderProductPage(locale, origin) : isSitemap ? renderSitemap(origin) : renderRobots(origin));
      });
    },
    generateBundle() {
      for (const locale of publicLocales) this.emitFile({ type: 'asset', fileName: `about/${locale}/index.html`, source: renderProductPage(locale, origin) });
      this.emitFile({ type: 'asset', fileName: 'robots.txt', source: renderRobots(origin) });
      if (origin) this.emitFile({ type: 'asset', fileName: 'sitemap.xml', source: renderSitemap(origin) });
    },
  };
}
export default defineConfig(({ mode }) => ({
  plugins: [react(), productPages(validatePublicOrigin(loadEnv(mode, '.', 'VITE_').VITE_PUBLIC_SITE_URL ?? ''))],
  base: './',
  server: { port: 5173, strictPort: true },
  preview: { port: 4173, strictPort: true },
}));
