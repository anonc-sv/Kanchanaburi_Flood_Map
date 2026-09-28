# แผนที่สถานการณ์น้ำ จังหวัดกาญจนบุรี

แผนที่เว็บที่อัปเดตตัวเองทุก 30 นาที แสดงระดับน้ำเทียบตลิ่ง ฝนสะสม 24 ชม. เขื่อน
และพื้นที่น้ำท่วมจากดาวเทียม ใช้ GitHub Actions ดึงข้อมูลและ GitHub Pages เผยแพร่ (ฟรี ไม่ต้องมีเซิร์ฟเวอร์)

## โครงสร้าง

```
index.html                    หน้าแผนที่ (Leaflet)
scripts/fetch_data.py         ดึงข้อมูลจาก สสน. + GISTDA → data/*.json (ใช้แค่ standard library)
data/boundary.geojson         ขอบเขตจังหวัดและ 13 อำเภอ (ไฟล์คงที่)
data/stations.json            ระดับน้ำ ฝน เขื่อน (สร้างอัตโนมัติ)
data/flood_{1day,3days,7days}.geojson  พื้นที่น้ำท่วมจากดาวเทียม (สร้างอัตโนมัติ)
data/history.json             ประวัติระดับน้ำย้อนหลัง 48 ชม. ต่อสถานี (สร้างอัตโนมัติ)
data/status.json              เวลาอัปเดตและสถานะแต่ละแหล่งข้อมูล
.github/workflows/update.yml  ตั้งเวลาดึงข้อมูลทุก 30 นาที แล้ว deploy
```

## ติดตั้ง (ประมาณ 15 นาที)

1. **สมัคร API key ของ GISTDA** ที่ https://api-gateway.gistda.or.th (ใช้สำหรับชั้นน้ำท่วมจากดาวเทียม
   ถ้ายังไม่มี key ระบบยังทำงานได้ แค่ไม่มีชั้นนี้)
2. สร้าง repository ใหม่บน GitHub แบบ **Public** (GitHub Pages ฟรีต้องเป็น public) แล้วอัปโหลดไฟล์ทั้งหมดในโฟลเดอร์นี้
   (รวมโฟลเดอร์ `.github` ด้วย — ถ้าอัปโหลดผ่านเว็บแล้วโฟลเดอร์ที่ขึ้นต้นด้วยจุดไม่ติดไป ให้ใช้ git หรือ GitHub Desktop)
3. **Settings → Secrets and variables → Actions → New repository secret**
   ชื่อ `GISTDA_API_KEY` ใส่ค่า key (เก็บเป็น secret จะไม่ถูกเปิดเผยในหน้าเว็บหรือไฟล์ข้อมูล)
4. **Settings → Pages → Build and deployment → Source** เลือก **GitHub Actions**
5. แท็บ **Actions** → เลือก workflow "อัปเดตข้อมูลและเผยแพร่แผนที่" → **Run workflow**
6. รอ 1–2 นาที ลิงก์เว็บจะอยู่ในหน้า Settings → Pages (รูปแบบ `https://<ชื่อผู้ใช้>.github.io/<ชื่อ repo>/`)

หลังจากนี้ระบบจะรันเองทุก 30 นาที

## ตั้งค่าเพิ่มเติม (Settings → Secrets and variables → Actions → Variables)

| ชื่อ | ค่าเริ่มต้น | ใช้ทำอะไร |
|---|---|---|
| `GISTDA_PERIODS` | `1day,3days,7days` | ช่วงเวลาภาพดาวเทียมที่ดึง |
| `THAIWATER_DAM_PATHS` | `analyst/dam,public/dam_load` | endpoint ข้อมูลเขื่อน ลองทีละตัว |

## ตรวจสอบหลังรันครั้งแรก (สำคัญ)

สคริปต์เขียนแบบทนต่อโครงสร้างข้อมูลที่เปลี่ยน แต่ทดสอบกับข้อมูลจำลองเท่านั้น
เพราะเครื่องที่ใช้พัฒนาเข้าถึงเว็บ สสน. และ GISTDA ไม่ได้ ให้เปิด `data/status.json` หลังรันครั้งแรกแล้วดูว่า

- `waterlevel.count` ควรมากกว่า 0 (สถานีในกาญจนบุรี) ถ้าเป็น 0 แต่ `upstream_rows` มีค่า แปลว่าชื่อฟิลด์ไม่ตรง
  ให้เปิด `https://api-v3.thaiwater.net/api/v1/thaiwater30/public/waterlevel_load` ในเบราว์เซอร์
  ดูชื่อฟิลด์จริง แล้วแก้ใน `fetch_waterlevel()`
- `dams.ok` — endpoint เขื่อนยังไม่ยืนยัน ถ้าขัดข้อง เปิด thaiwater.net หน้าเขื่อน กด F12 → Network
  ดู XHR ที่หน้าเว็บเรียก แล้วใส่ path นั้นใน variable `THAIWATER_DAM_PATHS`
- ถ้า `waterlevel`/`rain` ขึ้น `HTTPError 403` แปลว่าแหล่งข้อมูลบล็อก IP ต่างประเทศ
  ให้ใช้ self-hosted runner ในไทย (Settings → Actions → Runners) แล้วแก้ `runs-on` ใน workflow

## ทดสอบบนเครื่อง

```bash
GISTDA_API_KEY=xxxx python3 scripts/fetch_data.py
python3 -m http.server 8000     # แล้วเปิด http://localhost:8000
```

## ข้อควรรู้

- ระดับน้ำและฝนเป็นข้อมูลโทรมาตร อัปเดตทุก 10–60 นาทีตามสถานี
- พื้นที่น้ำท่วมมาจากดาวเทียมเรดาร์ อัปเดตตามรอบที่ดาวเทียมผ่าน (ทุก 1–3 วัน)
  และอาจมองไม่เห็นน้ำท่วมในเขตเมืองหรือใต้ต้นไม้หนาแน่น
- เกณฑ์สีระดับน้ำใช้ % ความจุลำน้ำตาม สสน. (>100% ล้นตลิ่ง, 70–100% น้ำมาก)
- ก่อนเผยแพร่สาธารณะ ตรวจเงื่อนไขการใช้ข้อมูลของ สสน. และ GISTDA และระบุแหล่งที่มาทุกครั้ง (หน้าเว็บระบุไว้แล้ว)
- แผนที่นี้ไม่ใช่ประกาศเตือนภัยทางการ
