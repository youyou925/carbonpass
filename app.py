from flask import Flask, request, render_template, redirect, url_for, send_from_directory, jsonify
import pytesseract
from PIL import Image
import os
import uuid
import datetime
import json

from cbam_calculator import CBAMCalculator
from report_generator import ReportGenerator

app = Flask(__name__)

# 配置上传文件夹
UPLOAD_FOLDER = 'uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# 报告文件夹
REPORTS_FOLDER = 'reports'
app.config['REPORTS_FOLDER'] = REPORTS_FOLDER

# 确保目录存在
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(REPORTS_FOLDER, exist_ok=True)

# Tesseract 路径配置（Windows用户需要修改这里）
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'


@app.route('/')
def home():
    """主页"""
    return render_template('home.html')


@app.route('/contact')
def contact():
    """联系页面"""
    return render_template('contact.html')


@app.route('/receipt', methods=['GET', 'POST'])
def receipt():
    """票据识别页面"""
    result = None
    image_path = None
    filename = None

    if request.method == 'POST':
        if 'image' not in request.files:
            return render_template('receipt.html', error='请选择要上传的图片文件')

        file = request.files['image']

        if file.filename == '':
            return render_template('receipt.html', error='请选择要上传的图片文件')

        ext = os.path.splitext(file.filename)[1]
        filename = f"{uuid.uuid4().hex}{ext}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        try:
            img = Image.open(filepath)
            width, height = img.size
            if width > height:
                img = img.rotate(90, expand=True)

            text = pytesseract.image_to_string(img, lang='chi_sim+eng')
            lines = [line.strip() for line in text.split('\n') if line.strip()]
            result = '\n'.join(lines)
            image_path = filepath

        except Exception as e:
            result = f"识别出错: {str(e)}"
            image_path = filepath if os.path.exists(filepath) else None

    return render_template('receipt.html',
                           result=result,
                           image_path=image_path,
                           filename=filename,
                           error=None)


@app.route('/carbon')
def carbon():
    """碳足迹核算页面"""
    return render_template('carbon.html')


@app.route('/cbam', methods=['GET', 'POST'])
def cbam_report():
    """CBAM 合规报告页面"""
    calculator = CBAMCalculator()
    generator = ReportGenerator()

    result_data = None

    if request.method == 'POST':
        # 获取表单数据
        company_name = request.form.get('company_name', '')
        facility_id = request.form.get('facility_id', '')
        facility_address = request.form.get('facility_address', '')
        contact_person = request.form.get('contact_person', '')
        contact_email = request.form.get('contact_email', '')
        reporting_period = request.form.get('reporting_period', '2026-Q1')
        production_period = request.form.get('production_period', '')

        product_type = request.form.get('product_type', 'steel')
        product_description = request.form.get('product_description', '')
        quantity = float(request.form.get('quantity', 0) or 0)
        unit = request.form.get('unit', 'tonnes')

        # 排放数据
        electricity_kwh = float(request.form.get('electricity_kwh', 0) or 0)
        diesel_litres = float(request.form.get('diesel_litres', 0) or 0)
        natural_gas_m3 = float(request.form.get('natural_gas_m3', 0) or 0)
        lpg_kg = float(request.form.get('lpg_kg', 0) or 0)
        coal_kg = float(request.form.get('coal_kg', 0) or 0)
        fuel_oil_litres = float(request.form.get('fuel_oil_litres', 0) or 0)

        # EU 进口信息
        eu_importer = request.form.get('eu_importer', '')
        import_country = request.form.get('import_country', '')
        import_quantity = float(request.form.get('import_quantity', quantity) or quantity)
        eu_carbon_price = float(request.form.get('eu_carbon_price', 85) or 85)

        # 计算排放
        has_actual_data = electricity_kwh > 0 or diesel_litres > 0 or natural_gas_m3 > 0

        if has_actual_data:
            calc_result = calculator.calculate_embedded_emissions(
                product_type=product_type,
                production_quantity=quantity,
                electricity_kwh=electricity_kwh,
                diesel_litres=diesel_litres,
                natural_gas_m3=natural_gas_m3,
                lpg_kg=lpg_kg,
                coal_kg=coal_kg,
                fuel_oil_litres=fuel_oil_litres
            )
            methodology = 'Actual data based on GHG Protocol'
            factor_source = 'Malaysian grid factor (0.7 kg/kWh) + IPCC AR6'
        else:
            calc_result = calculator.calculate_with_c_bam_defaults(
                product_type=product_type,
                production_quantity=quantity,
                has_actual_data=False
            )
            methodology = 'EU CBAM Default Values'
            factor_source = 'EU CBAM Default Values 2025'

        # CBAM 证书需求计算
        ee_per_tonne = calc_result['embedded_emissions_per_unit'] / 1000  # 转为 tonne
        cert_need = calculator.calculate_cbam_certificate_need(
            embedded_emissions_per_tonne=ee_per_tonne,
            quantity_imported=import_quantity if import_quantity > 0 else quantity,
            eu_ets_carbon_price=eu_carbon_price
        )

        # 构建报告数据
        report_data = {
            'report_id': f"CBAM-MY-{uuid.uuid4().hex[:8].upper()}",
            'reporting_period': reporting_period,
            'company_name': company_name,
            'facility_id': facility_id,
            'facility_address': facility_address,
            'country': 'MY',
            'contact_person': contact_person,
            'contact_email': contact_email,
            'product_type': product_type,
            'product_description': product_description,
            'quantity': quantity,
            'unit': unit,
            'production_period': production_period,
            'scope1_kg': calc_result['scope1_emissions_kg'],
            'scope2_kg': calc_result['scope2_emissions_kg'],
            'embedded_emissions_kg': calc_result['total_emissions_kg'],
            'emissions_per_tonne': calc_result['embedded_emissions_per_unit'],
            'methodology': methodology,
            'factor_source': factor_source,
            'eu_importer': eu_importer,
            'import_country': import_country,
            'import_quantity': import_quantity if import_quantity > 0 else quantity,
            'verified': False,
            'uncertainty_percent': calc_result['uncertainty_percent'],
            'data_quality': 'Measured' if has_actual_data else 'Default',
            'certificate_need': cert_need
        }

        # 生成 CBAM XML 报告
        xml_filename = f"CBAM_{company_name.replace(' ', '_')}_{reporting_period}.xml"
        xml_path = generator.generate_cbam_xml(report_data, xml_filename)

        # 生成 PDF 报告（如果 reportlab 可用）
        pdf_path = None
        try:
            pdf_filename = f"CBAM_{company_name.replace(' ', '_')}_{reporting_period}.pdf"
            pdf_path = generator.generate_pdf_report(report_data, pdf_filename)
        except ImportError:
            pass

        # 构建结果数据
        result_data = {
            'scope1_kg': f"{calc_result['scope1_emissions_kg']:,.2f}",
            'scope2_kg': f"{calc_result['scope2_emissions_kg']:,.2f}",
            'total_kg': f"{calc_result['total_emissions_kg']:,.2f}",
            'per_tonne': f"{calc_result['embedded_emissions_per_unit']:.2f}",
            'certificates_needed': int(cert_need['cbam_certificates_needed']),
            'carbon_price': eu_carbon_price,
            'cost_eur': f"{cert_need['total_cost_eur']:,.2f}",
            'xml_path': f"/download/{xml_filename}",
            'pdf_path': f"/download/{pdf_filename}" if pdf_path else None
        }

    # 获取排放因子摘要
    factors = calculator.get_factor_summary()

    return render_template('cbam_report.html',
                           result=result_data,
                           factors=factors)


@app.route('/reports')
def reports():
    """报告历史页面"""
    generator = ReportGenerator()
    report_list = generator.list_reports()
    return render_template('reports.html', reports=report_list)


@app.route('/download/<filename>')
def download_report(filename):
    """下载报告文件"""
    # 安全检查：只允许下载 reports 目录下的文件
    safe_filename = os.path.basename(filename)  # 防止路径穿越
    filepath = os.path.join(REPORTS_FOLDER, safe_filename)

    if os.path.exists(filepath):
        return send_from_directory(
            app.config['REPORTS_FOLDER'],
            safe_filename,
            as_attachment=True
        )
    else:
        return f"文件不存在: {safe_filename}", 404


@app.route('/uploads/<filename>')
def uploaded_file(filename):
    """显示上传的图片"""
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


@app.route('/api/factors')
def get_factors():
    """获取排放因子 API"""
    calculator = CBAMCalculator()
    return jsonify(calculator.get_factor_summary())


@app.route('/api/calculate', methods=['POST'])
def api_calculate():
    """API：快速计算碳排放"""
    calculator = CBAMCalculator()

    data = request.get_json()

    result = calculator.calculate_embedded_emissions(
        product_type=data.get('product_type', 'steel'),
        production_quantity=float(data.get('production_quantity', 0)),
        electricity_kwh=float(data.get('electricity_kwh', 0)),
        diesel_litres=float(data.get('diesel_litres', 0)),
        natural_gas_m3=float(data.get('natural_gas_m3', 0))
    )

    return jsonify(result)


if __name__ == '__main__':
    print("=" * 50)
    print("CarbonPass 碳合规平台已启动")
    print("请在浏览器中打开: http://127.0.0.1:5000")
    print("=" * 50)
    app.run(debug=True, port=5000)