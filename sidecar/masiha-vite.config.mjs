import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';
const here=path.dirname(new URL(import.meta.url).pathname);
export default defineConfig({
 plugins:[react()],
 base:'/masiha-ui/',
 publicDir:false,
 resolve:{alias:{'@':path.resolve(here,'src')}},
 build:{
   outDir:'/var/www/masiha-ui',
   emptyOutDir:true,
   rollupOptions:{input:path.resolve(here,'masiha-standalone.html')},
 },
});
