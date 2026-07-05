import React, { useState, useEffect } from 'react';
import axios from 'axios';
import {
    BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
    LineChart, Line, PieChart, Pie, Cell, AreaChart, Area
} from 'recharts';
import { LayoutDashboard, TrendingUp, AlertTriangle, Package, DollarSign, Wallet, Users, ArrowUpRight, ArrowDownRight } from 'lucide-react';

const COLORS = ['#0088FE', '#00C49F', '#FFBB28', '#FF8042', '#8884d8', '#82ca9d'];

function App() {
    const [activeTab, setActiveTab] = useState('overview');
    const [loading, setLoading] = useState(true);
    const [kpi, setKpi] = useState(null);
    const [salesTrend, setSalesTrend] = useState([]);
    const [financials, setFinancials] = useState([]);
    const [inventory, setInventory] = useState(null);
    const [topCustomers, setTopCustomers] = useState([]);
    const [selectedYear, setSelectedYear] = useState(new Date().getFullYear());
    const [legacyData, setLegacyData] = useState(null);
    const [currency, setCurrency] = useState('$');

    // Generate years for dropdown (e.g., current year - 4 to current year + 1)
    const currentYear = new Date().getFullYear();
    const years = Array.from({ length: 6 }, (_, i) => currentYear - 4 + i).reverse();

    // Fetch initial dashboard data (excluding sales trend which depends on year)
    useEffect(() => {
        const fetchData = async () => {
            try {
                const [kpiRes, finRes, invRes, custRes, legacyRes, settingsRes] = await Promise.all([
                    axios.get('/api/analysis/kpi-summary'),
                    axios.get('/api/analysis/financial-comparison'),
                    axios.get('/api/analysis/inventory-summary'),
                    axios.get('/api/analysis/top-customers'),
                    axios.get('/dashboard_data'),
                    axios.get('/api/company-settings')
                ]);

                setKpi(kpiRes.data);
                setFinancials(finRes.data);
                setInventory(invRes.data);
                setTopCustomers(custRes.data);
                setLegacyData(legacyRes.data);
                if (settingsRes.data && settingsRes.data.currency_code) {
                    setCurrency(settingsRes.data.currency_code);
                }
            } catch (error) {
                console.error("Error fetching dashboard data", error);
            } finally {
                setLoading(false);
            }
        };

        fetchData();
    }, []);

    // Fetch sales trend when year changes
    useEffect(() => {
        const fetchSalesTrend = async () => {
            try {
                const response = await axios.get(`/api/analysis/sales-trend?year=${selectedYear}`);
                setSalesTrend(response.data);
            } catch (error) {
                console.error("Error fetching sales trend", error);
            }
        };

        fetchSalesTrend();
    }, [selectedYear]);

    if (loading) return (
        <div className="flex items-center justify-center h-screen bg-gray-50 flex-col gap-4">
            <div className="w-12 h-12 border-4 border-blue-500 border-t-transparent rounded-full animate-spin"></div>
            <div className="text-xl text-gray-600 font-medium">Loading Analysis...</div>
        </div>
    );

    return (
        <div className="min-h-screen bg-gray-50 p-6 font-sans">
            <div className="max-w-7xl mx-auto space-y-8">

                {/* Header */}
                <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
                    <div>
                        <h1 className="text-3xl font-bold text-gray-800 flex items-center gap-3">
                            <LayoutDashboard className="w-8 h-8 text-blue-600" />
                            Analysis Dashboard
                        </h1>
                        <p className="text-gray-500 mt-1">Real-time financial insights and performance metrics</p>
                    </div>

                    <div className="flex bg-white rounded-lg p-1 shadow-sm border border-gray-200">
                        {['Overview', 'Financials', 'Vouchers', 'Inventory'].map(tab => (
                            <button
                                key={tab}
                                onClick={() => setActiveTab(tab.toLowerCase())}
                                className={`px-6 py-2 rounded-md text-sm font-medium transition-all duration-200 ${activeTab === tab.toLowerCase()
                                    ? 'bg-blue-600 text-white shadow-md transform scale-105'
                                    : 'text-gray-600 hover:bg-gray-50 hover:text-blue-600'
                                    }`}
                            >
                                {tab}
                            </button>
                        ))}
                    </div>
                </div>

                {/* KPI Cards */}
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
                    <KpiCard
                        title="Cash Balance"
                        value={legacyData?.cash_balance}
                        icon={Wallet}
                        color="green"
                        currency={currency}
                    />
                    <KpiCard
                        title="Bank Balance"
                        value={legacyData?.bank_balance}
                        icon={Wallet}
                        color="blue"
                        currency={currency}
                    />
                    <KpiCard
                        title="Receivables"
                        value={kpi?.total_receivables}
                        icon={ArrowDownRight}
                        color="orange"
                        currency={currency}
                    />
                    <KpiCard
                        title="Payables"
                        value={kpi?.total_payables}
                        icon={ArrowUpRight}
                        color="red"
                        currency={currency}
                    />
                </div>

                {/* Overview Tab Content */}
                {activeTab === 'overview' && (
                    <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
                        {/* Sales Trend Chart */}
                        <div className="lg:col-span-2 bg-white p-6 rounded-xl shadow-sm border border-gray-100">
                            <div className="flex justify-between items-center mb-6">
                                <h2 className="text-lg font-bold text-gray-800 flex items-center gap-2">
                                    <TrendingUp className="w-5 h-5 text-blue-500" />
                                    Monthly Sales Trend
                                </h2>
                                <select
                                    className="border border-gray-300 rounded-md px-3 py-1 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
                                    value={selectedYear}
                                    onChange={(e) => setSelectedYear(Number(e.target.value))}
                                >
                                    {years.map(year => (
                                        <option key={year} value={year}>{year}</option>
                                    ))}
                                </select>
                            </div>
                            <div className="h-80 w-full">
                                <ResponsiveContainer width="100%" height="100%">
                                    <AreaChart data={salesTrend}>
                                        <defs>
                                            <linearGradient id="colorSales" x1="0" y1="0" x2="0" y2="1">
                                                <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.8} />
                                                <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
                                            </linearGradient>
                                        </defs>
                                        <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f0f0f0" />
                                        <XAxis dataKey="name" axisLine={false} tickLine={false} tick={{ fill: '#9ca3af', fontSize: 12 }} dy={10} />
                                        <YAxis axisLine={false} tickLine={false} tick={{ fill: '#9ca3af', fontSize: 12 }} tickFormatter={(value) => `${currency}${value / 1000}k`} />
                                        <Tooltip
                                            contentStyle={{ backgroundColor: '#fff', borderRadius: '8px', border: 'none', boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.1)' }}
                                            formatter={(value) => [`${currency} ${value.toLocaleString()}`, 'Sales']}
                                        />
                                        <Area type="monotone" dataKey="value" stroke="#3b82f6" strokeWidth={3} fillOpacity={1} fill="url(#colorSales)" />
                                    </AreaChart>
                                </ResponsiveContainer>
                            </div>
                        </div>

                        {/* Top Customers */}
                        <div className="lg:col-span-1 bg-white p-6 rounded-xl shadow-sm border border-gray-100 flex flex-col">
                            <h2 className="text-lg font-bold text-gray-800 mb-6 flex items-center gap-2">
                                <Users className="w-5 h-5 text-purple-500" />
                                Top Customers
                            </h2>
                            <div className="flex-1 overflow-auto pr-2 space-y-4">
                                {topCustomers.map((customer, index) => (
                                    <div key={index} className="flex items-center justify-between p-3 hover:bg-gray-50 rounded-lg transition-colors group">
                                        <div className="flex items-center gap-3">
                                            <div className={`w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold text-white shadow-sm ${index < 3 ? 'bg-yellow-400' : 'bg-gray-300'}`}>
                                                {index + 1}
                                            </div>
                                            <span className="font-medium text-gray-700 group-hover:text-blue-600 transition-colors truncate max-w-[120px]" title={customer.name}>
                                                {customer.name}
                                            </span>
                                        </div>
                                        <span className="font-bold text-gray-900">{currency} {customer.value.toLocaleString()}</span>
                                    </div>
                                ))}
                                {topCustomers.length === 0 && (
                                    <div className="text-center text-gray-400 py-10">No customer data available</div>
                                )}
                            </div>
                        </div>
                    </div>
                )}

                {/* Financials Tab Content */}
                {activeTab === 'financials' && (
                    <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
                        <h2 className="text-lg font-bold text-gray-800 mb-6 flex items-center gap-2">
                            <DollarSign className="w-5 h-5 text-green-500" />
                            Income vs Expenses
                        </h2>
                        <div className="h-96 w-full">
                            <ResponsiveContainer width="100%" height="100%">
                                <BarChart data={financials}>
                                    <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f0f0f0" />
                                    <XAxis dataKey="month" axisLine={false} tickLine={false} tick={{ fill: '#9ca3af' }} dy={10} />
                                    <YAxis axisLine={false} tickLine={false} tick={{ fill: '#9ca3af' }} />
                                    <Tooltip
                                        cursor={{ fill: '#f9fafb' }}
                                        contentStyle={{ backgroundColor: '#fff', borderRadius: '8px', border: 'none', boxShadow: '0 10px 15px -3px rgba(0, 0, 0, 0.1)' }}
                                    />
                                    <Legend iconType="circle" wrapperStyle={{ paddingTop: '20px' }} />
                                    <Bar dataKey="Income" fill="#10b981" radius={[4, 4, 0, 0]} barSize={20} />
                                    <Bar dataKey="Expense" fill="#ef4444" radius={[4, 4, 0, 0]} barSize={20} />
                                </BarChart>
                            </ResponsiveContainer>
                        </div>
                    </div>
                )}

                {/* Vouchers Tab Content */}
                {activeTab === 'vouchers' && legacyData && (
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                        {[
                            { title: 'Sales', count: legacyData.sales_count, amount: legacyData.sales_amount, color: 'blue' },
                            { title: 'Purchase', count: legacyData.purchase_count, amount: legacyData.purchase_amount, color: 'indigo' },
                            { title: 'Receipt', count: legacyData.receipt_count, amount: legacyData.receipt_amount, color: 'green' },
                            { title: 'Payment', count: legacyData.payment_count, amount: legacyData.payment_amount, color: 'red' },
                            { title: 'Contra', count: legacyData.contra_count, amount: legacyData.contra_amount, color: 'purple' },
                            { title: 'Journal', count: legacyData.journal_count, amount: legacyData.journal_amount, color: 'orange' },
                            { title: 'Expense', count: legacyData.expense_count, amount: legacyData.expense_amount, color: 'yellow' },
                            { title: 'Sales Return', count: legacyData.sales_return_count, amount: legacyData.sales_return_amount, color: 'pink' },
                            { title: 'Purchase Return', count: legacyData.purchase_return_count, amount: legacyData.purchase_return_amount, color: 'teal' },
                        ].map((item) => (
                            <div key={item.title} className={`bg-white p-6 rounded-xl shadow-sm border border-gray-100 border-l-4 border-${item.color}-500`}>
                                <div className="flex justify-between items-start">
                                    <h3 className="font-semibold text-gray-700">{item.title}</h3>
                                    <span className="text-xs bg-gray-100 text-gray-600 px-2 py-1 rounded-full">{item.count} Vouchers</span>
                                </div>
                                <div className="mt-4">
                                    <p className="text-2xl font-bold text-gray-900">{currency} {(item.amount || 0).toLocaleString()}</p>
                                </div>
                            </div>
                        ))}
                    </div>
                )}

                {/* Inventory Tab Content */}
                {activeTab === 'inventory' && (
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
                        <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
                            <h2 className="text-lg font-bold text-gray-800 mb-6 flex items-center gap-2">
                                <Package className="w-5 h-5 text-orange-500" />
                                Stock Distribution (Value)
                            </h2>
                            <div className="h-80 w-full flex items-center justify-center">
                                <ResponsiveContainer width="100%" height="100%">
                                    <PieChart>
                                        <Pie
                                            data={inventory?.category_distribution}
                                            cx="50%"
                                            cy="50%"
                                            innerRadius={60}
                                            outerRadius={100}
                                            fill="#8884d8"
                                            paddingAngle={5}
                                            dataKey="value"
                                        >
                                            {inventory?.category_distribution?.map((entry, index) => (
                                                <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                                            ))}
                                        </Pie>
                                        <Tooltip formatter={(value) => `${currency} ${value.toLocaleString()}`} />
                                        <Legend />
                                    </PieChart>
                                </ResponsiveContainer>
                            </div>
                        </div>

                        <div className="space-y-6">
                            <div className="grid grid-cols-2 gap-4">
                                <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100 border-l-4 border-l-red-500">
                                    <h3 className="text-gray-500 font-medium text-xs uppercase tracking-wider">Zero Stock</h3>
                                    <div className="mt-2 text-3xl font-extrabold text-gray-800">{legacyData?.zero_stock || 0}</div>
                                </div>
                                <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100 border-l-4 border-l-red-800">
                                    <h3 className="text-gray-500 font-medium text-xs uppercase tracking-wider">Negative Stock</h3>
                                    <div className="mt-2 text-3xl font-extrabold text-gray-800">{legacyData?.negative_stock || 0}</div>
                                </div>
                                <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100 border-l-4 border-l-orange-500">
                                    <h3 className="text-gray-500 font-medium text-xs uppercase tracking-wider">Critical (≤5)</h3>
                                    <div className="mt-2 text-3xl font-extrabold text-gray-800">{legacyData?.stock_critical || 0}</div>
                                </div>
                                <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100 border-l-4 border-l-yellow-500">
                                    <h3 className="text-gray-500 font-medium text-xs uppercase tracking-wider">Low Stock (≤10)</h3>
                                    <div className="mt-2 text-3xl font-extrabold text-gray-800">{legacyData?.stock_warning || 0}</div>
                                </div>
                            </div>

                            <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100 border-l-4 border-l-blue-500">
                                <div className="flex items-center justify-between">
                                    <div>
                                        <h3 className="text-gray-500 font-medium text-sm uppercase tracking-wider">Total Inventory Value</h3>
                                        <div className="mt-4">
                                            <span className="text-3xl font-extrabold text-gray-800">{currency} {(inventory?.total_stock_value || 0).toLocaleString()}</span>
                                        </div>
                                    </div>
                                    <Wallet className="w-8 h-8 text-blue-500 opacity-80" />
                                </div>
                            </div>

                            <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
                                <div className="flex items-center justify-between">
                                    <div>
                                        <h3 className="text-gray-500 font-medium text-sm uppercase tracking-wider">Slow Moving Items</h3>
                                        <p className="text-sm text-gray-400 font-light mt-1">Found in last 90 days</p>
                                    </div>
                                    <div className="text-2xl font-bold text-gray-800">{inventory?.slow_moving_count || 0}</div>
                                </div>
                            </div>
                        </div>
                    </div>
                )}

            </div>
        </div>
    );
}

function KpiCard({ title, value, icon: Icon, color, trend, currency }) {
    const colorClasses = {
        blue: 'bg-blue-50 text-blue-600',
        green: 'bg-green-50 text-green-600',
        red: 'bg-red-50 text-red-600',
        indigo: 'bg-indigo-50 text-indigo-600',
        orange: 'bg-orange-50 text-orange-600',
        purple: 'bg-purple-50 text-purple-600',
    };

    return (
        <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100 hover:shadow-md transition-shadow">
            <div className="flex items-center justify-between mb-4">
                <div className={`p-3 rounded-lg ${colorClasses[color]}`}>
                    <Icon className="w-6 h-6" />
                </div>
                {trend && (
                    <span className="text-xs font-semibold text-green-600 bg-green-50 px-2 py-1 rounded-full">
                        {trend}
                    </span>
                )}
            </div>
            <div>
                <h3 className="text-gray-500 font-medium text-sm">{title}</h3>
                <p className="text-2xl font-bold text-gray-900 mt-1">
                    {currency || '$'} {(value || 0).toLocaleString()}
                </p>
            </div>
        </div>
    );
}

export default App;
