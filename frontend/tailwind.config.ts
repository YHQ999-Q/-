import type { Config } from 'tailwindcss'

export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        bee: {
          yellow: '#FFDC00',
          lemon: '#FFE66B',
          cream: '#FFF8D8',
          ink: '#11120D',
          panel: '#1A1B17',
          muted: '#92948A',
        },
      },
      fontFamily: {
        sans: ['Inter', 'PingFang SC', 'Microsoft YaHei', 'system-ui', 'sans-serif'],
      },
      boxShadow: {
        phone: '0 30px 90px rgba(0,0,0,.42)',
        yellow: '0 14px 36px rgba(255,220,0,.22)',
      },
    },
  },
  plugins: [],
} satisfies Config
