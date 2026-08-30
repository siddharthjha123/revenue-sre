import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'
import { resolve } from 'node:path'

// https://vite.dev/config/
export default defineConfig(({mode})=>{const root=resolve(import.meta.dirname,'..');const env=loadEnv(mode,root,'');return{plugins:[react()],define:{__MERCHANT_ID__:JSON.stringify(env.MERCHANT_ID??'')},build:{chunkSizeWarningLimit:800},server:{host:'127.0.0.1',port:5173,proxy:{'/server':{target:'http://127.0.0.1:8000',changeOrigin:true,rewrite:(path)=>path.replace(/^\/server/,'')}}}}})
