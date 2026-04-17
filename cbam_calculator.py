"""
CarbonPass - CBAM Carbon Calculator
计算符合欧盟 CBAM 规范的碳排放量
"""

import json
import os
from datetime import datetime
from typing import Dict, List, Optional, Tuple


class CBAMCalculator:
    """CBAM 碳排放计算引擎"""

    def __init__(self, emission_factors_path: str = None):
        if emission_factors_path is None:
            emission_factors_path = os.path.join(
                os.path.dirname(__file__),
                'emission_factors.json'
            )

        with open(emission_factors_path, 'r', encoding='utf-8') as f:
            self.factors = json.load(f)

    def calculate_embedded_emissions(
        self,
        product_type: str,
        production_quantity: float,
        electricity_kwh: float = 0,
        diesel_litres: float = 0,
        natural_gas_m3: float = 0,
        lpg_kg: float = 0,
        coal_kg: float = 0,
        fuel_oil_litres: float = 0,
        raw_material_kg: float = 0,
        raw_material_type: str = None,
        use马来西亚默认因子: bool = True
    ) -> Dict:
        """
        计算产品隐含碳排放 (Embedded Emissions)

        参数:
            product_type: 产品类型 (steel/aluminum/cement/fertilizer/hydrogen/electricity)
            production_quantity: 产量 (tonnes 或 MWh for electricity)
            electricity_kwh: 用电量 (kWh)
            diesel_litres: 柴油消耗 (litres)
            natural_gas_m3: 天然气消耗 (m³)
            lpg_kg: LPG消耗 (kg)
            coal_kg: 煤炭消耗 (kg)
            fuel_oil_litres: 燃油消耗 (litres)
            raw_material_kg: 原材料消耗 (kg)
            raw_material_type: 原材料类型
            use马来西亚默认因子: 是否使用马来西亚本地因子（否则用CBAM默认值）

        返回:
            包含计算结果的字典
        """

        result = {
            'product_type': product_type,
            'production_quantity': production_quantity,
            'calculation_date': datetime.now().isoformat(),
            'methodology': 'CBAM',
            'scope1_emissions_kg': 0,
            'scope2_emissions_kg': 0,
            'total_emissions_kg': 0,
            'embedded_emissions_per_unit': 0,
            'unit': 'kg CO2 per unit',
            'emission_breakdown': {},
            'uncertainty_percent': 0
        }

        # Scope 1: 直接燃烧排放
        scope1_total = 0
        fuels_used = {}

        if diesel_litres > 0:
            emission = diesel_litres * self.factors['fuels']['diesel']['factor']
            scope1_total += emission
            fuels_used['diesel'] = {
                'amount': diesel_litres,
                'unit': 'litres',
                'emission_kg': emission
            }

        if natural_gas_m3 > 0:
            emission = natural_gas_m3 * self.factors['fuels']['natural_gas']['factor']
            scope1_total += emission
            fuels_used['natural_gas'] = {
                'amount': natural_gas_m3,
                'unit': 'm³',
                'emission_kg': emission
            }

        if lpg_kg > 0:
            emission = lpg_kg * self.factors['fuels']['lpg']['factor']
            scope1_total += emission
            fuels_used['lpg'] = {
                'amount': lpg_kg,
                'unit': 'kg',
                'emission_kg': emission
            }

        if coal_kg > 0:
            emission = coal_kg * self.factors['fuels']['coal_bituminous']['factor']
            scope1_total += emission
            fuels_used['coal'] = {
                'amount': coal_kg,
                'unit': 'kg',
                'emission_kg': emission
            }

        if fuel_oil_litres > 0:
            emission = fuel_oil_litres * self.factors['fuels']['fuel_oil']['factor']
            scope1_total += emission
            fuels_used['fuel_oil'] = {
                'amount': fuel_oil_litres,
                'unit': 'litres',
                'emission_kg': emission
            }

        result['scope1_emissions_kg'] = scope1_total
        result['emission_breakdown']['scope1'] = fuels_used

        # Scope 2: 电力消耗排放
        scope2_total = 0
        if electricity_kwh > 0:
            grid_factor = self.factors['electricity_grid']['malaysia_grid']['factor']
            scope2_total = electricity_kwh * grid_factor
            result['emission_breakdown']['scope2'] = {
                'electricity_kwh': electricity_kwh,
                'grid_factor': grid_factor,
                'emission_kg': scope2_total
            }

        result['scope2_emissions_kg'] = scope2_total

        # 原材料排放（如果是外购的半成品材料）
        raw_material_emission = 0
        if raw_material_kg > 0 and raw_material_type:
            cbam_default_key = raw_material_type.lower()
            if cbam_default_key in self.factors['cbam_defaults']:
                emission_per_kg = self.factors['cbam_defaults'][cbam_default_key]['factor'] * 1000  # 转换为kg
                raw_material_emission = raw_material_kg * emission_per_kg
                result['emission_breakdown']['raw_material'] = {
                    'type': raw_material_type,
                    'amount_kg': raw_material_kg,
                    'emission_kg': raw_material_emission
                }

        # 总排放
        result['total_emissions_kg'] = scope1_total + scope2_total + raw_material_emission

        # 单位产品隐含排放
        if production_quantity > 0:
            if product_type == 'electricity':
                # 电力按 MWh 计算
                result['embedded_emissions_per_unit'] = result['total_emissions_kg'] / (production_quantity / 1000)  # kWh to MWh
                result['unit'] = 'kg CO2 per MWh'
            else:
                result['embedded_emissions_per_unit'] = result['total_emissions_kg'] / production_quantity
                result['unit'] = 'kg CO2 per tonne'

        # 计算不确定度
        uncertainty_components = []
        if electricity_kwh > 0:
            uncertainty_components.append(5)  # electricity data: ±5%
        if diesel_litres > 0 or natural_gas_m3 > 0 or coal_kg > 0:
            uncertainty_components.append(3)  # fuel consumption: ±3%
        if production_quantity > 0:
            uncertainty_components.append(2)  # production quantity: ±2%
        uncertainty_components.append(10)  # emission factor: ±10%

        # 平方和根法合成不确定度
        import math
        total_uncertainty = math.sqrt(sum(u**2 for u in uncertainty_components))
        result['uncertainty_percent'] = round(total_uncertainty, 1)

        return result

    def calculate_with_c_bam_defaults(
        self,
        product_type: str,
        production_quantity: float,
        has_actual_data: bool = False,
        **kwargs
    ) -> Dict:
        """
        使用 CBAM 默认值或实际数据计算
        """

        if has_actual_data:
            return self.calculate_embedded_emissions(
                product_type=product_type,
                production_quantity=production_quantity,
                **kwargs
            )
        else:
            # 使用 CBAM 默认值
            default_key = product_type.lower()
            if default_key in self.factors['cbam_defaults']:
                default_ee = self.factors['cbam_defaults'][default_key]['factor']
                total_emissions = default_ee * production_quantity  # tonne CO2

                return {
                    'product_type': product_type,
                    'production_quantity': production_quantity,
                    'calculation_date': datetime.now().isoformat(),
                    'methodology': 'CBAM Default Values',
                    'scope1_emissions_kg': 0,
                    'scope2_emissions_kg': 0,
                    'total_emissions_kg': total_emissions * 1000,  # 转为kg
                    'embedded_emissions_per_unit': default_ee,
                    'unit': 'tonne CO2 per tonne',
                    'emission_breakdown': {
                        'note': 'Using EU CBAM default values, no actual data provided'
                    },
                    'uncertainty_percent': 15
                }
            else:
                raise ValueError(f"Unknown product type: {product_type}")

    def calculate_malaysia_mrv(
        self,
        facility_name: str,
        reporting_year: int,
        production_data: Dict,
        emissions_data: Dict
    ) -> Dict:
        """
        计算马来西亚 MRV 格式的排放报告

        参数:
            facility_name: 设施名称
            reporting_year: 报告年份
            production_data: 生产数据 {'product_type': quantity, ...}
            emissions_data: 排放数据 {'electricity_kwh': ..., 'diesel_litres': ..., ...}
        """

        mrv_result = {
            'facility_name': facility_name,
            'reporting_year': reporting_year,
            'report_date': datetime.now().isoformat(),
            'total_scope1_kg': 0,
            'total_scope2_kg': 0,
            'total_emissions_kg': 0,
            'products': []
        }

        for product_type, quantity in production_data.items():
            elec = emissions_data.get('electricity_kwh', 0)
            diesel = emissions_data.get('diesel_litres', 0)
            ng = emissions_data.get('natural_gas_m3', 0)

            calc_result = self.calculate_embedded_emissions(
                product_type=product_type,
                production_quantity=quantity,
                electricity_kwh=elec,
                diesel_litres=diesel,
                natural_gas_m3=ng
            )

            mrv_result['products'].append({
                'type': product_type,
                'quantity': quantity,
                'emissions_kg': calc_result['total_emissions_kg']
            })

            mrv_result['total_scope1_kg'] += calc_result['scope1_emissions_kg']
            mrv_result['total_scope2_kg'] += calc_result['scope2_emissions_kg']

        mrv_result['total_emissions_kg'] = (
            mrv_result['total_scope1_kg'] + mrv_result['total_scope2_kg']
        )

        return mrv_result

    def calculate_cbam_certificate_need(
        self,
        embedded_emissions_per_tonne: float,
        quantity_imported: float,
        eu_ets_carbon_price: float = 85.0
    ) -> Dict:
        """
        计算需要的 CBAM 证书数量和成本

        参数:
            embedded_emissions_per_tonne: 每吨产品的隐含排放 (tonne CO2/tonne)
            quantity_imported: 进口数量 (tonnes)
            eu_ets_carbon_price: EU ETS 碳价 (EUR/tonne CO2)

        返回:
            包含证书需求信息的字典
        """

        total_emissions = embedded_emissions_per_tonne * quantity_imported  # tonne CO2

        return {
            'total_emissions_tonne': round(total_emissions, 3),
            'cbam_certificates_needed': round(total_emissions, 0),
            'price_per_certificate_eur': eu_ets_carbon_price,
            'total_cost_eur': round(total_emissions * eu_ets_carbon_price, 2),
            'equivalent_rm': round(total_emissions * eu_ets_carbon_price * 4.8, 2),  # 假设 EUR/RM = 4.8
            'cost_per_tonne_product_eur': eu_ets_carbon_price * embedded_emissions_per_tonne
        }

    def get_product_info(self, product_type: str) -> Optional[Dict]:
        """获取产品信息"""
        return self.factors['product_types'].get(product_type.lower())

    def get_factor_summary(self) -> Dict:
        """获取排放因子摘要"""
        return {
            'malaysia_grid': self.factors['electricity_grid']['malaysia_grid'],
            'fuels': {k: {'factor': v['factor'], 'unit': v['unit']}
                     for k, v in self.factors['fuels'].items()},
            'cbam_defaults': {k: {'factor': v['factor'], 'unit': v['unit']}
                             for k, v in self.factors['cbam_defaults'].items()}
        }


def create_sample_calculation():
    """创建示例计算"""

    calc = CBAMCalculator()

    # 示例：马来西亚钢铁厂
    result = calc.calculate_embedded_emissions(
        product_type='steel',
        production_quantity=1000,  # 1000 tonnes
        electricity_kwh=500000,  # 50万度电
        diesel_litres=10000,  # 1万升柴油
        natural_gas_m3=50000,  # 5万m³天然气
        coal_kg=200000  # 20万kg煤炭
    )

    print("=== CarbonPass CBAM 计算结果 ===")
    print(f"产品类型: {result['product_type']}")
    print(f"产量: {result['production_quantity']} tonnes")
    print(f"Scope 1 (直接排放): {result['scope1_emissions_kg']:.2f} kg CO2")
    print(f"Scope 2 (电力): {result['scope2_emissions_kg']:.2f} kg CO2")
    print(f"总排放: {result['total_emissions_kg']:.2f} kg CO2")
    print(f"单位产品隐含排放: {result['embedded_emissions_per_unit']:.3f} kg CO2/tonne")
    print(f"不确定度: ±{result['uncertainty_percent']}%")

    # CBAM 证书计算
    cert_need = calc.calculate_cbam_certificate_need(
        embedded_emissions_per_tonne=result['embedded_emissions_per_unit'] / 1000,  # 转为tonne
        quantity_imported=1000,
        eu_ets_carbon_price=85.0
    )

    print("\n=== CBAM 证书需求 ===")
    print(f"总排放: {cert_need['total_emissions_tonne']} tonne CO2")
    print(f"证书需求: {cert_need['cbam_certificates_needed']} 个")
    print(f"总成本: €{cert_need['total_cost_eur']} (约 RM {cert_need['equivalent_rm']})")


if __name__ == '__main__':
    create_sample_calculation()