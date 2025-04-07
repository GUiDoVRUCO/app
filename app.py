from flask import Flask, render_template, request, redirect, url_for, jsonify
from flask_mysqldb import MySQL
from flask_bcrypt import Bcrypt
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from datetime import datetime, timedelta
import calendar

app = Flask(__name__)
app.secret_key = 'segredo_super_secreto'

# Configuração do MySQL
app.config['MYSQL_HOST'] = 'localhost'
app.config['MYSQL_USER'] = 'root'
app.config['MYSQL_PASSWORD'] = ''
app.config['MYSQL_DB'] = 'barbeariapy'
app.config['MYSQL_CURSORCLASS'] = 'DictCursor'

mysql = MySQL(app)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# Função para converter timedelta para string no formato HH:MM
def timedelta_to_str(td):
    if td is None:
        return None
    total_seconds = int(td.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    return f"{hours:02d}:{minutes:02d}"

# Modelo de Usuário
class Usuario(UserMixin):
    def __init__(self, id, nome, email, is_admin):
        self.id = id
        self.nome = nome
        self.email = email
        self.is_admin = is_admin

    def get_id(self):
        return str(self.id)

@login_manager.user_loader
def load_user(user_id):
    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM usuarios WHERE id = %s", (user_id,))
    user = cur.fetchone()
    cur.close()
    return Usuario(user['id'], user['nome'], user['email'], user['is_admin']) if user else None

# Menu (Página Inicial)
@app.route('/')
@login_required
def menu():
    return render_template('index.html', usuario=current_user)

# Cadastro
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        nome = request.form['nome']
        email = request.form['email']
        senha = request.form['senha']
        senha_hash = bcrypt.generate_password_hash(senha).decode('utf-8')
        cur = mysql.connection.cursor()
        cur.execute("INSERT INTO usuarios (nome, email, senha, is_admin) VALUES (%s, %s, %s, %s)", (nome, email, senha_hash, 0))
        mysql.connection.commit()
        cur.close()
        return redirect(url_for('login'))
    return render_template('register.html')

# Login
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email, senha = request.form['email'], request.form['senha']
        cur = mysql.connection.cursor()
        cur.execute("SELECT * FROM usuarios WHERE email = %s", (email,))
        user = cur.fetchone()
        cur.close()
        if user and bcrypt.check_password_hash(user['senha'], senha):
            login_user(Usuario(user['id'], user['nome'], user['email'], user['is_admin']))
            return redirect(url_for('menu'))
    return render_template('login.html')

# Logout
@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# Configurar Horários (Admin) - Apenas API para processar os dados
@app.route('/admin/config-horarios', methods=['POST'])
@login_required
def config_horarios():
    if not current_user.is_admin:
        return jsonify({'success': False, 'message': 'Acesso não autorizado.'}), 403

    cur = mysql.connection.cursor()

    try:
        data = request.get_json()
        dias_semana = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo']
        intervalo_agendamento = int(data.get('intervalo_agendamento', 30))

        for dia in dias_semana:
            fechado = data.get(f'fechado_{dia}') == 'on'
            hora_abertura = data.get(f'hora_abertura_{dia}') if not fechado else None
            hora_fechamento = data.get(f'hora_fechamento_{dia}') if not fechado else None

            if not fechado and hora_abertura and hora_fechamento:
                try:
                    if len(hora_abertura.split(':')) == 3:
                        hora_abertura = hora_abertura[:-3]
                    if len(hora_fechamento.split(':')) == 3:
                        hora_fechamento = hora_fechamento[:-3]
                    hora_abertura_dt = datetime.strptime(hora_abertura, '%H:%M')
                    hora_fechamento_dt = datetime.strptime(hora_fechamento, '%H:%M')
                    if hora_abertura_dt >= hora_fechamento_dt:
                        cur.close()
                        return jsonify({'success': False, 'message': f'Erro: A hora de abertura deve ser anterior à hora de fechamento para {dia}.'}), 400
                except ValueError as e:
                    cur.close()
                    return jsonify({'success': False, 'message': f'Erro: Formato de hora inválido para {dia}. Use o formato HH:MM (ex.: 09:00). Erro: {str(e)}'}), 400
            elif not fechado and (not hora_abertura or not hora_fechamento):
                cur.close()
                return jsonify({'success': False, 'message': f'Erro: Por favor, preencha os horários de abertura e fechamento para {dia}.'}), 400

            cur.execute("""
                UPDATE configuracoes 
                SET hora_abertura = %s, hora_fechamento = %s, fechado = %s, intervalo_agendamento = %s 
                WHERE dia_semana = %s
            """, (hora_abertura, hora_fechamento, fechado, intervalo_agendamento, dia))
        
        mysql.connection.commit()
        cur.execute("SELECT * FROM configuracoes ORDER BY FIELD(dia_semana, 'Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo')")
        configuracoes_atualizadas = cur.fetchall()
        for config in configuracoes_atualizadas:
            config['hora_abertura'] = timedelta_to_str(config['hora_abertura'])
            config['hora_fechamento'] = timedelta_to_str(config['hora_fechamento'])
        cur.execute("SELECT intervalo_agendamento FROM configuracoes LIMIT 1")
        intervalo_atualizado = cur.fetchone()['intervalo_agendamento']
        cur.close()
        return jsonify({
            'success': True,
            'message': 'Horários atualizados com sucesso!',
            'configuracoes': configuracoes_atualizadas,
            'intervalo_agendamento': intervalo_atualizado
        })
    except Exception as e:
        mysql.connection.rollback()
        cur.close()
        return jsonify({'success': False, 'message': f'Erro ao atualizar os horários: {str(e)}'}), 500

# Rota para buscar os horários disponíveis
@app.route('/atualizar-horarios-disponiveis', methods=['GET'])
@login_required
def atualizar_horarios_disponiveis():
    data_selecionada = request.args.get('data', datetime.today().strftime('%Y-%m-%d'))
    try:
        dia_semana = datetime.strptime(data_selecionada, '%Y-%m-%d').weekday()
    except ValueError as e:
        return jsonify({'success': False, 'message': f'Data inválida: {str(e)}'}), 400

    dias_semana = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo']
    dia_semana_nome = dias_semana[dia_semana]
    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM configuracoes WHERE dia_semana = %s", (dia_semana_nome,))
    config = cur.fetchone()
    horarios_disponiveis = []
    intervalo_agendamento = 30
    if config and not config['fechado']:
        if config['hora_abertura'] and config['hora_fechamento']:
            try:
                hora_abertura_str = timedelta_to_str(config['hora_abertura'])
                hora_fechamento_str = timedelta_to_str(config['hora_fechamento'])
                hora_inicio = datetime.strptime(hora_abertura_str, '%H:%M')
                hora_fim = datetime.strptime(hora_fechamento_str, '%H:%M')
                intervalo_agendamento = config['intervalo_agendamento']
                if intervalo_agendamento <= 0:
                    intervalo_agendamento = 30
                current_time = hora_inicio
                while current_time < hora_fim:
                    horarios_disponiveis.append(current_time.strftime('%H:%M'))
                    current_time += timedelta(minutes=intervalo_agendamento)
            except ValueError as e:
                return jsonify({'success': False, 'message': f'Formato de hora inválido nas configurações para {dia_semana_nome}. Erro: {str(e)}'}), 500
    cur.execute("SELECT horario FROM agendamentos WHERE data = %s AND status = %s", (data_selecionada, 'Ativo'))
    agendamentos = cur.fetchall()
    horarios_ocupados = [agendamento['horario'] for agendamento in agendamentos]
    cur.close()
    response = jsonify({
        'success': True,
        'horarios_disponiveis': horarios_disponiveis,
        'horarios_ocupados': horarios_ocupados
    })
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response

# Agendamento
@app.route('/agendar', methods=['GET', 'POST'])
@login_required
def agendar():
    if request.method == 'POST':
        data = request.form['data']
        horario = request.form['horario']
        servico = request.form['servico']
        usuario_id = current_user.id
        cur = mysql.connection.cursor()
        cur.execute("INSERT INTO agendamentos (usuario_id, data, horario, servico, status) VALUES (%s, %s, %s, %s, %s)", 
                    (usuario_id, data, horario, servico, 'Ativo'))
        mysql.connection.commit()
        cur.close()
        return redirect(url_for('menu'))

    data_selecionada = request.args.get('data', datetime.today().strftime('%Y-%m-%d'))
    try:
        dia_semana = datetime.strptime(data_selecionada, '%Y-%m-%d').weekday()
    except ValueError:
        return "Erro: Data inválida.", 400

    dias_semana = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo']
    dia_semana_nome = dias_semana[dia_semana]
    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM configuracoes WHERE dia_semana = %s", (dia_semana_nome,))
    config = cur.fetchone()
    horarios_disponiveis = []
    intervalo_agendamento = 30
    if config and not config['fechado']:
        if config['hora_abertura'] and config['hora_fechamento']:
            try:
                hora_abertura_str = timedelta_to_str(config['hora_abertura'])
                hora_fechamento_str = timedelta_to_str(config['hora_fechamento'])
                hora_inicio = datetime.strptime(hora_abertura_str, '%H:%M')
                hora_fim = datetime.strptime(hora_fechamento_str, '%H:%M')
                intervalo_agendamento = config['intervalo_agendamento']
                if intervalo_agendamento <= 0:
                    intervalo_agendamento = 30
                current_time = hora_inicio
                while current_time < hora_fim:
                    horarios_disponiveis.append(current_time.strftime('%H:%M'))
                    current_time += timedelta(minutes=intervalo_agendamento)
            except ValueError as e:
                return f"Erro: Formato de hora inválido nas configurações para {dia_semana_nome}. Erro: {str(e)}", 500
    cur.execute("SELECT horario FROM agendamentos WHERE data = %s AND status = %s", (data_selecionada, 'Ativo'))
    agendamentos = cur.fetchall()
    horarios_ocupados = [agendamento['horario'] for agendamento in agendamentos]
    cur.execute("SELECT * FROM configuracoes ORDER BY FIELD(dia_semana, 'Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo')")
    configuracoes = cur.fetchall()
    for config in configuracoes:
        config['hora_abertura'] = timedelta_to_str(config['hora_abertura'])
        config['hora_fechamento'] = timedelta_to_str(config['hora_fechamento'])
    cur.close()
    return render_template('agendamentos.html', usuario=current_user, 
                         horarios_disponiveis=horarios_disponiveis, 
                         horarios_ocupados=horarios_ocupados,
                         data_selecionada=data_selecionada,
                         configuracoes=configuracoes,
                         intervalo_agendamento=intervalo_agendamento)

# Painel do Cliente
@app.route('/client-panel')
@login_required
def client_panel():
    agora = datetime.now()
    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM agendamentos WHERE usuario_id = %s ORDER BY data, horario", (current_user.id,))
    agendamentos = cur.fetchall()
    cur.close()
    agendamentos_futuros = []
    agendamentos_passados = []
    for agendamento in agendamentos:
        data_horario_str = f"{agendamento['data']} {agendamento['horario']}:00"
        try:
            data_horario_dt = datetime.strptime(data_horario_str, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            continue
        
        if data_horario_dt >= agora and agendamento['status'] == 'Ativo':
            agendamentos_futuros.append(agendamento)
        else:
            agendamentos_passados.append(agendamento)
    return render_template('client-panel.html', usuario=current_user, 
                         agendamentos_futuros=agendamentos_futuros, 
                         agendamentos_passados=agendamentos_passados)

# Cancelar Agendamento (Cliente)
@app.route('/cancelar-agendamento/<int:agendamento_id>', methods=['POST'])
@login_required
def cancelar_agendamento(agendamento_id):
    motivo = request.form.get('motivo')
    cur = mysql.connection.cursor()
    cur.execute("UPDATE agendamentos SET status = %s, motivo_cancelamento = %s WHERE id = %s AND usuario_id = %s", 
                ('Cancelado', motivo, agendamento_id, current_user.id))
    mysql.connection.commit()
    cur.close()
    return redirect(url_for('client_panel'))

# Painel do Administrador
@app.route('/admin/painel', methods=['GET', 'POST'])
@login_required
def admin_painel():
    if not current_user.is_admin:
        return redirect(url_for('menu'))

    agora = datetime.now()
    hoje = agora.strftime('%Y-%m-%d')
    mes_atual = agora.strftime('%Y-%m')

    # Calcular o início e fim da semana atual (segunda a domingo)
    dia_da_semana = agora.weekday()  # 0 = segunda, 6 = domingo
    inicio_semana = agora - timedelta(days=dia_da_semana)  # Início da semana (segunda)
    fim_semana = inicio_semana + timedelta(days=6)  # Fim da semana (domingo)
    inicio_semana_str = inicio_semana.strftime('%Y-%m-%d')
    fim_semana_str = fim_semana.strftime('%Y-%m-%d')

    cur = mysql.connection.cursor()

    # Buscar configurações
    cur.execute("SELECT * FROM configuracoes ORDER BY FIELD(dia_semana, 'Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo')")
    configuracoes = cur.fetchall()
    for config in configuracoes:
        config['hora_abertura'] = timedelta_to_str(config['hora_abertura'])
        config['hora_fechamento'] = timedelta_to_str(config['hora_fechamento'])

    # Intervalo de agendamento
    cur.execute("SELECT intervalo_agendamento FROM configuracoes LIMIT 1")
    intervalo_result = cur.fetchone()
    intervalo_agendamento = intervalo_result['intervalo_agendamento'] if intervalo_result else 30

    # 1. Status dos Agendamentos
    cur.execute("""
        SELECT COUNT(*) as total 
        FROM agendamentos a 
        JOIN usuarios u ON a.usuario_id = u.id 
        WHERE a.data = %s AND a.status NOT IN ('Concluído', 'Arquivado') AND a.horario >= %s
    """, (hoje, agora.strftime('%H:%M')))
    cortes_faltam = cur.fetchone()['total']

    cur.execute("""
        SELECT COUNT(*) as total 
        FROM agendamentos a 
        JOIN usuarios u ON a.usuario_id = u.id 
        WHERE a.data = %s AND a.status = 'Concluído'
    """, (hoje,))
    cortes_concluidos = cur.fetchone()['total']

    cur.execute("""
        SELECT a.*, u.nome AS cliente_nome 
        FROM agendamentos a 
        JOIN usuarios u ON a.usuario_id = u.id 
        WHERE a.data = %s AND a.status NOT IN ('Concluído', 'Arquivado') AND a.horario >= %s 
        ORDER BY a.horario LIMIT 5
    """, (hoje, agora.strftime('%H:%M')))
    proximos_clientes = cur.fetchall()

    cur.execute("""
        SELECT a.*, u.nome AS cliente_nome 
        FROM agendamentos a 
        JOIN usuarios u ON a.usuario_id = u.id 
        WHERE a.data = %s AND a.status NOT IN ('Concluído', 'Arquivado') AND a.horario < %s
    """, (hoje, agora.strftime('%H:%M')))
    atrasados_raw = cur.fetchall()
    atrasados = []
    for cliente in atrasados_raw:
        data_horario_str = f"{cliente['data']} {cliente['horario']}:00"
        try:
            data_horario_dt = datetime.strptime(data_horario_str, '%Y-%m-%d %H:%M:%S')
            atraso = (agora - data_horario_dt).total_seconds() / 60
            cliente['atraso'] = round(atraso)
            atrasados.append(cliente)
        except ValueError:
            continue

    # 2. Financeiro
    precos = {'Corte Clássico': 40.00, 'Corte Degradê': 45.00, 'Barba Completa': 30.00, 'Corte + Barba': 65.00, 'Sobrancelha': 20.00}

    # Quanto já recebeu na semana
    cur.execute("""
        SELECT a.servico 
        FROM agendamentos a 
        WHERE a.data BETWEEN %s AND %s AND a.status = 'Concluído'
    """, (inicio_semana_str, fim_semana_str))
    agendamentos_semana = cur.fetchall()
    recebido_semana = sum(precos.get(agendamento['servico'], 0) for agendamento in agendamentos_semana)

    # Quanto já recebeu hoje
    cur.execute("""
        SELECT a.servico 
        FROM agendamentos a 
        WHERE a.data = %s AND a.status = 'Concluído'
    """, (hoje,))
    agendamentos_hoje = cur.fetchall()
    recebido_hoje = sum(precos.get(agendamento['servico'], 0) for agendamento in agendamentos_hoje)

    # Total recebido no mês
    cur.execute("""
        SELECT a.servico 
        FROM agendamentos a 
        WHERE a.data LIKE %s AND a.status = 'Concluído'
    """, (mes_atual + '%',))
    agendamentos_mes = cur.fetchall()
    total_mes = sum(precos.get(agendamento['servico'], 0) for agendamento in agendamentos_mes)

    # Média de faturamento
    dias_no_mes = calendar.monthrange(agora.year, agora.month)[1]
    media_diaria = total_mes / dias_no_mes if dias_no_mes > 0 else 0
    media_mensal = total_mes

    cur.execute("""
        SELECT a.servico, COUNT(*) as total 
        FROM agendamentos a 
        WHERE a.data LIKE %s AND a.status = 'Concluído' 
        GROUP BY a.servico 
        ORDER BY total DESC LIMIT 3
    """, (mes_atual + '%',))
    servicos_lucrativos = cur.fetchall()

    # Dados para os gráficos financeiros
    meses = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago']
    cur.execute("SELECT mes, receita, despesa FROM financeiro WHERE ano = %s", (agora.year,))
    financeiro_data = cur.fetchall()
    if not financeiro_data:
        receitas = [200, 150, 300, 100, 250, 200, 180, 220]
        despesas = [-50, -100, -80, -120, -90, -110, -70, -130]
        for i, mes in enumerate(meses):
            cur.execute("INSERT INTO financeiro (ano, mes, receita, despesa) VALUES (%s, %s, %s, %s)", 
                        (agora.year, mes, receitas[i], abs(despesas[i])))
        mysql.connection.commit()
    else:
        receitas = [0] * len(meses)
        despesas = [0] * len(meses)
        for data in financeiro_data:
            idx = meses.index(data['mes'])
            receitas[idx] = data['receita']
            despesas[idx] = -data['despesa']

    if request.method == 'POST':
        for i, mes in enumerate(meses):
            receita = float(request.form.get(f'receita_{mes}', receitas[i]))
            despesa = float(request.form.get(f'despesa_{mes}', abs(despesas[i])))
            cur.execute("""
                UPDATE financeiro 
                SET receita = %s, despesa = %s 
                WHERE ano = %s AND mes = %s
            """, (receita, despesa, agora.year, mes))
        mysql.connection.commit()
        return redirect(url_for('admin_painel'))

    orcamento = {
        'meta': 5000,
        'progresso': [total_mes * (i + 1) / len(meses) for i in range(len(meses))]
    }

    cur.execute("""
        SELECT a.servico, u.nome AS cliente_nome, a.data, COUNT(*) as total, SUM(CASE 
            WHEN a.servico = 'Corte Clássico' THEN 40.00 
            WHEN a.servico = 'Corte Degradê' THEN 45.00 
            WHEN a.servico = 'Barba Completa' THEN 30.00 
            WHEN a.servico = 'Corte + Barba' THEN 65.00 
            WHEN a.servico = 'Sobrancelha' THEN 20.00 
            ELSE 0 END) as receita 
        FROM agendamentos a 
        JOIN usuarios u ON a.usuario_id = u.id 
        WHERE a.status = 'Concluído' 
        GROUP BY a.servico, u.nome, a.data 
        ORDER BY a.data DESC LIMIT 3
    """)
    pedidos_recentes = cur.fetchall()

    cur.execute("""
        SELECT a.id, a.servico, a.data, a.horario, 
               CASE 
                   WHEN a.servico = 'Corte Clássico' THEN 40.00 
                   WHEN a.servico = 'Corte Degradê' THEN 45.00 
                   WHEN a.servico = 'Barba Completa' THEN 30.00 
                   WHEN a.servico = 'Corte + Barba' THEN 65.00 
                   WHEN a.servico = 'Sobrancelha' THEN 20.00 
                   ELSE 0 
               END as valor 
        FROM agendamentos a 
        WHERE a.status = 'Concluído' 
        ORDER BY a.data DESC, a.horario DESC LIMIT 3
    """)
    transacoes = cur.fetchall()

    # 3. Desempenho e Eficiência
    media_tempo_corte = 30
    cur.execute("""
        SELECT SUBSTRING(horario, 1, 2) as hora, COUNT(*) as total 
        FROM agendamentos 
        WHERE data LIKE %s AND status = 'Concluído' 
        GROUP BY hora 
        ORDER BY total DESC LIMIT 2
    """, (mes_atual + '%',))
    horarios_pico = cur.fetchall()
    if len(horarios_pico) >= 2:
        hora_inicio = f"{horarios_pico[0]['hora']}:00"
        hora_fim = f"{horarios_pico[1]['hora']}:00"
        if int(horarios_pico[0]['hora']) > int(horarios_pico[1]['hora']):
            hora_inicio, hora_fim = hora_fim, hora_inicio
        horario_pico = f"das {hora_inicio} às {hora_fim}"
        total_agendamentos_pico = sum(h['total'] for h in horarios_pico)
        cur.execute("SELECT COUNT(*) as total FROM agendamentos WHERE data LIKE %s AND status = 'Concluído'", (mes_atual + '%',))
        total_agendamentos = cur.fetchone()['total']
        horario_pico_percent = (total_agendamentos_pico / total_agendamentos * 100) if total_agendamentos > 0 else 0
    else:
        horario_pico = "N/A"
        horario_pico_percent = 0

    cur.execute("""
        SELECT DAYNAME(data) as dia, COUNT(*) as total 
        FROM agendamentos 
        WHERE data LIKE %s AND status = 'Concluído' 
        GROUP BY dia 
        ORDER BY total DESC LIMIT 1
    """, (mes_atual + '%',))
    dia_mais_clientes = cur.fetchone()
    dia_mais_clientes = dia_mais_clientes['dia'] if dia_mais_clientes else "N/A"

    cur.execute("SELECT COUNT(*) as total FROM agendamentos WHERE data LIKE %s AND status = 'Cancelado'", (mes_atual + '%',))
    cancelados = cur.fetchone()['total']

    # 4. Clientes e Fidelização
    cur.execute("""
        SELECT a.*, u.nome AS cliente_nome 
        FROM agendamentos a 
        JOIN usuarios u ON a.usuario_id = u.id 
        WHERE a.status = 'Arquivado' 
        ORDER BY a.data DESC, a.horario DESC LIMIT 3
    """)
    historico_cortes = cur.fetchall()

    # 5. Controle do Tempo
    cur.execute("""
        SELECT a.horario 
        FROM agendamentos a 
        WHERE a.data = %s AND a.status NOT IN ('Concluído', 'Arquivado') AND a.horario >= %s 
        ORDER BY a.horario LIMIT 1
    """, (hoje, agora.strftime('%H:%M')))
    proximo = cur.fetchone()
    if proximo:
        proximo_horario = datetime.strptime(f"{hoje} {proximo['horario']}:00", '%Y-%m-%d %H:%M:%S')
        tempo_falta = (proximo_horario - agora).total_seconds() / 60
    else:
        tempo_falta = None

    tempo_espera_medio = 15
    cur.execute("""
        SELECT horario 
        FROM agendamentos 
        WHERE data = %s AND status NOT IN ('Concluído', 'Arquivado') 
        ORDER BY horario
    """, (hoje,))
    horarios_hoje = [agendamento['horario'] for agendamento in cur.fetchall()]
    pausas = []
    if horarios_hoje:
        for i in range(len(horarios_hoje) - 1):
            inicio = datetime.strptime(horarios_hoje[i], '%H:%M')
            fim = datetime.strptime(horarios_hoje[i + 1], '%H:%M')
            if (fim - inicio).total_seconds() / 60 > intervalo_agendamento:
                pausas.append(f"{horarios_hoje[i]} - {horarios_hoje[i + 1]}")

    cur.close()

    return render_template('admin_painel.html', usuario=current_user,
                         cortes_faltam=cortes_faltam, cortes_concluidos=cortes_concluidos, proximos_clientes=proximos_clientes, atrasados=atrasados,
                         recebido_hoje=recebido_hoje, recebido_semana=recebido_semana, media_diaria=media_diaria, media_mensal=media_mensal, 
                         servicos_lucrativos=servicos_lucrativos, media_tempo_corte=media_tempo_corte, horario_pico=horario_pico, 
                         horario_pico_percent=horario_pico_percent, dia_mais_clientes=dia_mais_clientes, cancelados=cancelados,
                         historico_cortes=historico_cortes, configuracoes=configuracoes, intervalo_agendamento=intervalo_agendamento,
                         tempo_falta=tempo_falta, tempo_espera_medio=tempo_espera_medio, pausas=pausas,
                         meses=meses, receitas=receitas, despesas=despesas, orcamento=orcamento,
                         pedidos_recentes=pedidos_recentes, transacoes=transacoes)

# Resetar Cortes Concluídos
@app.route('/admin/resetar-cortes-concluidos', methods=['POST'])
@login_required
def resetar_cortes_concluidos():
    if not current_user.is_admin:
        return jsonify({'success': False, 'message': 'Acesso não autorizado.'}), 403

    try:
        hoje = datetime.now().strftime('%Y-%m-%d')
        cur = mysql.connection.cursor()
        cur.execute("""
            UPDATE agendamentos 
            SET status = 'Arquivado' 
            WHERE data = %s AND status = 'Concluído'
        """, (hoje,))
        mysql.connection.commit()
        afetados = cur.rowcount
        cur.close()
        return jsonify({
            'success': True, 
            'message': f'{afetados} corte(s) concluído(s) foram movidos para o histórico com sucesso!'
        })
    except Exception as e:
        mysql.connection.rollback()
        return jsonify({'success': False, 'message': f'Erro ao mover cortes concluídos para o histórico: {str(e)}'}), 500

# Detalhes dos Cancelamentos
@app.route('/admin/cancelamentos', methods=['GET'])
@login_required
def cancelamentos():
    if not current_user.is_admin:
        return redirect(url_for('menu'))

    mes_atual = datetime.now().strftime('%Y-%m')
    cur = mysql.connection.cursor()
    cur.execute("""
        SELECT a.*, u.nome AS cliente_nome 
        FROM agendamentos a 
        JOIN usuarios u ON a.usuario_id = u.id 
        WHERE a.data LIKE %s AND a.status = 'Cancelado'
    """, (mes_atual + '%',))
    cancelamentos = cur.fetchall()
    cur.close()
    return render_template('cancelamentos.html', usuario=current_user, cancelamentos=cancelamentos)

# Todas as Transações
@app.route('/admin/transacoes')
@login_required
def todas_transacoes():
    if not current_user.is_admin:
        return redirect(url_for('menu'))

    precos = {'Corte Clássico': 40.00, 'Corte Degradê': 45.00, 'Barba Completa': 30.00, 'Corte + Barba': 65.00, 'Sobrancelha': 20.00}
    cur = mysql.connection.cursor()
    cur.execute("""
        SELECT a.id, a.servico, a.data, a.horario, 
               CASE 
                   WHEN a.servico = 'Corte Clássico' THEN 40.00 
                   WHEN a.servico = 'Corte Degradê' THEN 45.00 
                   WHEN a.servico = 'Barba Completa' THEN 30.00 
                   WHEN a.servico = 'Corte + Barba' THEN 65.00 
                   WHEN a.servico = 'Sobrancelha' THEN 20.00 
                   ELSE 0 
               END as valor 
        FROM agendamentos a 
        WHERE a.status = 'Concluído' 
        ORDER BY a.data DESC, a.horario DESC
    """)
    todas_transacoes = cur.fetchall()
    cur.close()
    return render_template('todas_transacoes.html', usuario=current_user, transacoes=todas_transacoes)

# Cancelar Agendamento (Admin - AJAX)
@app.route('/cancel_appointment', methods=['POST'])
@login_required
def cancel_appointment():
    if not current_user.is_admin:
        return jsonify({'success': False, 'message': 'Acesso não autorizado.'}), 403

    data = request.get_json()
    appointment_id = data.get('appointment_id')
    if not appointment_id:
        return jsonify({'success': False, 'message': 'ID do agendamento não fornecido.'}), 400

    try:
        cur = mysql.connection.cursor()
        cur.execute("SELECT * FROM agendamentos WHERE id = %s AND status = 'Ativo'", (appointment_id,))
        agendamento = cur.fetchone()
        if not agendamento:
            cur.close()
            return jsonify({'success': False, 'message': 'Agendamento não encontrado ou já cancelado.'}), 404
        cur.execute("UPDATE agendamentos SET status = %s, motivo_cancelamento = %s WHERE id = %s", 
                    ('Cancelado', 'Cancelado pelo administrador', appointment_id))
        mysql.connection.commit()
        cur.close()
        return jsonify({'success': True, 'message': 'Agendamento cancelado com sucesso!'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Erro ao cancelar o agendamento: {str(e)}'}), 500

# Concluir Agendamento (Admin - AJAX)
@app.route('/complete_appointment', methods=['POST'])
@login_required
def complete_appointment():
    if not current_user.is_admin:
        return jsonify({'success': False, 'message': 'Acesso não autorizado.'}), 403

    data = request.get_json()
    appointment_id = data.get('appointment_id')
    if not appointment_id:
        return jsonify({'success': False, 'message': 'ID do agendamento não fornecido.'}), 400

    try:
        cur = mysql.connection.cursor()
        cur.execute("SELECT * FROM agendamentos WHERE id = %s AND status = 'Ativo'", (appointment_id,))
        agendamento = cur.fetchone()
        if not agendamento:
            cur.close()
            return jsonify({'success': False, 'message': 'Agendamento não encontrado ou já concluído/cancelado.'}), 404
        cur.execute("UPDATE agendamentos SET status = %s WHERE id = %s", 
                    ('Concluído', appointment_id))
        mysql.connection.commit()
        cur.close()
        return jsonify({'success': True, 'message': 'Agendamento concluído com sucesso!'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Erro ao concluir o agendamento: {str(e)}'}), 500

# Cancelar Agendamento (Admin - Form)
@app.route('/admin/cancelar/<int:agendamento_id>', methods=['POST'])
@login_required
def admin_cancelar_agendamento(agendamento_id):
    if not current_user.is_admin:
        return redirect(url_for('menu'))

    motivo = request.form.get('motivo')
    cur = mysql.connection.cursor()
    cur.execute("UPDATE agendamentos SET status = %s, motivo_cancelamento = %s WHERE id = %s", 
                ('Cancelado', motivo, agendamento_id))
    mysql.connection.commit()
    cur.close()
    return redirect(url_for('admin_painel'))

if __name__ == '__main__':
    app.run(debug=True)