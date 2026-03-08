# Stock API — Deploy lên Render.com

## Endpoints

| Endpoint | Mô tả |
|---|---|
| `GET /` | Health check |
| `GET /vnindex` | Dữ liệu VN-Index |
| `GET /stock/PVT` | Dữ liệu cổ phiếu PVT (thay tên tùy ý) |
| `GET /sectors` | Dòng tiền theo ngành |
| `GET /macro` | XAUUSD, DXY |
| `GET /news` | Tin tức thị trường |
| `GET /summary?symbols=PVT,GMD` | Tổng hợp tất cả |

## Environment Variables (cần set trên Render)

```
ALPHA_VANTAGE_KEY=your_key_here
NEWS_API_KEY=your_key_here
```

## Deploy

1. Push code lên GitHub
2. Tạo Web Service trên Render.com
3. Connect GitHub repo
4. Set environment variables
5. Deploy!
